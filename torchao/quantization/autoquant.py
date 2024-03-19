import torch
import os
from subprocess import check_output
from .subclass import ( # noqa
    Int8DynamicallyQuantizedLinearWeight,
    Int8WeightOnlyQuantizedLinearWeight,
    QuantizedLinearWeightBase,
)
from torch.utils._python_dispatch import return_and_correct_aliasing
from .quant_primitives import (
    quantize_activation_per_token_absmax,
    safe_int_mm,
)
import torch.nn.functional as F
from torch._inductor.utils import do_bench
aten = torch.ops.aten

AUTOQUANT_CACHE = {}

def check_cache(cls, shapes_and_dtype):
    return AUTOQUANT_CACHE.get((cls,)+shapes_and_dtype, None)

def update_cache(cls, shapes_and_dtype, res):
    AUTOQUANT_CACHE[(cls,)+shapes_and_dtype] = res

class AutoQuantizableLinearWeight(torch.Tensor):
    """
    when run, finds best type of quantization for this tensor and swaps itself with that
    """
    @staticmethod
    def __new__(cls, weight, qtensor_class_list, *args, mode=["relu", None], **kwargs):
        kwargs["device"] = weight.device
        kwargs["layout"] = (
            kwargs.get("layout") if kwargs.get("layout", False) else weight.layout
        )
        kwargs["dtype"] = (
            kwargs.get("dtype") if kwargs.get("dtype", False) else weight.dtype
        )
        kwargs["requires_grad"] = False
        shape = kwargs.pop("shape", weight.shape)
        return torch.Tensor._make_wrapper_subclass(cls, shape, **kwargs)  # type: ignore[attr-defined]

    def __init__(self, weight, qtensor_class_list, *args, mode=["relu", None], **kwargs):
        self.weight = weight
        self.qtensor_class_list = qtensor_class_list
        self.logged_data = {}
        self.mode = mode

    def __repr__(self):
        return (
            f"{self.__class__.__name__}(data={self.weight}, shape={self.shape}, "
            f"device={self.device}, dtype={self.dtype}, qtensor_class_list={self.qtensor_class_list})"
        )

    @staticmethod
    def log_shape(act_mat, w_autoquant, bias):
        act_mat = act_mat.reshape(-1, act_mat.shape[-1])
        logged_dtype = act_mat.dtype
        logged_shapes = (act_mat.shape, w_autoquant.shape, None if bias is None else  bias.shape,)
        shapes_and_dtype = logged_shapes + (logged_dtype,)
        w_autoquant.logged_data[shapes_and_dtype] = 1 + w_autoquant.logged_data.get(shapes_and_dtype, 0)
        for q_cls in w_autoquant.qtensor_class_list:
            if check_cache(q_cls, shapes_and_dtype) is None:
                update_cache(q_cls, shapes_and_dtype, None)

    def tune_autoquant(self, q_cls, shapes_and_dtype, best_time):
        act_shape, w_shape, bias_shape, act_dtype = shapes_and_dtype
        if check_cache(q_cls, shapes_and_dtype) is None:
            with torch.no_grad():
                act_mat = torch.randn(act_shape, dtype=act_dtype, device=self.device)
                bias = None if bias_shape is None else torch.randn(bias_shape, dtype=act_dtype, device=self.device)
                res = q_cls._autoquant_test(act_mat, self.weight, bias, best_time, self.mode)
                update_cache(q_cls, shapes_and_dtype, res)

    def to_quantized(self, error_on_unseen, **kwargs):
        if error_on_unseen and self.logged_data == {}:
            raise RuntimeError("must run module normally to get shape, dtype info for autoquant")
        elif (self.logged_data == {}) and not error_on_unseen:
            # default back to non-quantized weight if not seen
            self = AQFloatLinearWeight.from_float(self.weight)
            return self
        best_time = torch.inf
        best_cls = None
        do_print=False
        # check each class
        for q_cls in self.qtensor_class_list:
            # for each logged shape+dtype, benchmark
            cls_res=0
            for shapes_and_dtype, times_seen in self.logged_data.items():
                if check_cache(q_cls, shapes_and_dtype) is None:
                    do_print=True
                    self.tune_autoquant(q_cls, shapes_and_dtype, best_time)
                    torch._dynamo.reset()
                cls_res += check_cache(q_cls, shapes_and_dtype) * times_seen
            if best_time >= cls_res:
                best_time = cls_res
                best_cls = q_cls
        # only print if this is the first time seeing some cls+shape combo,
        # otherwise we will print the same thing for every layer.
        if do_print:
            print(f"for {self.logged_data}, best_cls={best_cls}")
        # TODO handle random cls args/kwargs? or should they be curried?
        self = best_cls.from_float(self.weight)
        return self

    def _apply_fn_to_data(self, fn):
        return self.__class__(
            fn(self.weight), self.qtensor_class_list, dtype=self.dtype, mode=self.mode
        )

    def __tensor_flatten__(self):
        return ["weight"], [self.qtensor_class_list, self.mode, self.dtype, self.shape]

    @classmethod
    def __tensor_unflatten__(cls, tensor_data_dict, tensor_attributes, outer_size=None, outer_stride=None):
        weight = tensor_data_dict["weight"]
        qtensor_class_list, mode, dtype, shape = tensor_attributes[0]
        return cls(weight, qtensor_class_list, mode, shape=shape if outer_size is None else outer_size, dtype=dtype, strides=outer_stride)

    @classmethod
    def from_float(cls, weight, qtensor_class_list, **kwargs):
        return cls(weight, qtensor_class_list, **kwargs)

    @classmethod
    def __torch_function__(cls, func, types, args=(), kwargs=None):
        kwargs = {} if kwargs is None else kwargs

        if func is torch.nn.functional.linear:
            mat1, w_autoquant, bias = (
                args[0],
                args[1],
                args[2] if len(args)>2 else None
            )
            cls.log_shape(mat1, w_autoquant, bias)
            return func(mat1, w_autoquant.weight, bias)
        try:
            with torch._C.DisableTorchFunctionSubclass():
                return func(*args, **kwargs)
        except:
            print(f"ERR: subclass doesn't implement {func}")

    @classmethod
    def __torch_dispatch__(cls, func, types, args, kwargs):
         if func is aten.detach.default:
            return return_and_correct_aliasing(func, args, kwargs, args[0]._apply_fn_to_data(torch.detach))

def do_autoquant_bench(op, *args, **kwargs):
    rep = kwargs.pop("rep", 100)
    warmup = kwargs.pop("warmup", 25)
    with torch.no_grad():
        torch.cuda.synchronize()
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            op(*args)
        stream.synchronize()
        torch.cuda.current_stream().wait_stream(stream)
        torch.cuda.synchronize()

        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, stream=stream):
            op(*args)
        res = do_bench(lambda: graph.replay(), warmup=warmup, rep=rep, return_mode="median")
    return res

def _is_interpolate_mode(mode):
    if isinstance(mode, list) and mode[0]=="interpolate" and len(mode)==2 and isinstance(mode[1], float):
        return True
    return False

class AQMixin():
    """
    Mixin to turn normal quantized subclasses into autoquantizable ones
    """
    @classmethod
    def _autoquant_test(cls, act_mat, weight, bias, best_time, mode=["relu", None]):
        w_qtensor = cls.from_float(weight)
        if _is_interpolate_mode(mode):
            q_c_op = torch.compile(cls._quantized_op, mode="max-autotune-no-cudagraphs")
        else:
            func = lambda a,b,c: F.relu(cls._quantized_op(F.relu(a), b, c))
            q_c_op = torch.compile(func, mode="max-autotune-no-cudagraphs")
        res = do_autoquant_bench(q_c_op, act_mat, w_qtensor, bias)
        if res < best_time*1.1:
            res2 = do_autoquant_bench(q_c_op, act_mat, w_qtensor, bias, warmup=25, rep=900)
            res=(res2*.9+res*.1)
        print(f"time: {res:0.3f}ms for {cls}, to_beat: {best_time:0.3f}ms ")
        return res

class AQInt8DynamicallyQuantizedLinearWeight(AQMixin, Int8DynamicallyQuantizedLinearWeight):
    """
    AutoQuantizable version of Int8DynamicallyQuantizedLinearWeight
    """
    @classmethod
    def _autoquant_test(cls, act_mat, weight, bias, best_time, mode=["relu", None]):
        if not _is_interpolate_mode(mode):
            return super()._autoquant_test(act_mat, weight, bias, best_time, mode)

        # SAM best is between .8 to 1, SDXL also performs best in this range
        INTERPOLATION_CONSTANT = mode[1]
        w_qtensor = cls.from_float(weight)
        x_vals_int8, x_scales = quantize_activation_per_token_absmax(
            act_mat.reshape(-1, act_mat.shape[-1])
        )
        quantized_matmul = (
            lambda x_vals_int8, x_scales, w_vals_int8:
                safe_int_mm(x_vals_int8, w_vals_int8) * x_scales
        )
        q_c_matmul=torch.compile(quantized_matmul, mode="max-autotune-no-cudagraphs")
        with torch.no_grad():
            res_matmul = do_autoquant_bench(q_c_matmul, x_vals_int8, x_scales, w_qtensor.int_data)
        print(f"time: {res_matmul:0.3f}ms for {cls} matmul, to_beat: {best_time:0.3f}ms")

        # if the (much faster) matmul kernel is already beat, don't bother benchmarking full op
        if res_matmul>=best_time:
            return res_matmul

        # calculate what time full op needs to beat for dynamic quant to be best given INTERPOLATION_CONSTANT
        to_beat = best_time + INTERPOLATION_CONSTANT/(1-INTERPOLATION_CONSTANT)*(best_time-res_matmul)
        res = super()._autoquant_test(act_mat, weight, bias, to_beat)
        max_int_const_win = (best_time-res_matmul)/(res-res_matmul)
        res_f = INTERPOLATION_CONSTANT*res+(1-INTERPOLATION_CONSTANT)*res_matmul
        print(f"time: {res_f:0.3f}ms for {cls} interpolated, breakeven constant: {max_int_const_win:0.2f}")
        return res_f

class AQWeightOnlyQuantizedLinearWeight(Int8WeightOnlyQuantizedLinearWeight, AQMixin):
    """
    AutoQuantizable version of Int8WeightOnlyQuantizedLinearWeight
    """

class AQWeightOnlyQuantizedLinearWeight2(Int8WeightOnlyQuantizedLinearWeight, AQMixin):
    """
    AutoQuantizable version of Int8WeightOnlyQuantizedLinearWeight that
    uses a different kernel
    """
    @staticmethod
    def _quantized_op(act_mat, w_qtensor, bias):
        orig_dtype = act_mat.dtype
        orig_shape = act_mat.shape
        act_mat = act_mat.reshape(-1, act_mat.shape[-1], 1)
        y = (act_mat*w_qtensor.int_data.unsqueeze(0)).sum(dim=-2)
        y = y.reshape(*orig_shape[:-1], y.shape[-1]) * w_qtensor.q_scales
        if bias is not None:
            y += bias
        return y.to(orig_dtype)

    @classmethod
    def _autoquant_test(cls, act_mat, *args):
        # if act_mat has batchsize>2 don't use this kernel
        if act_mat.reshape(-1, act_mat.shape[-1]).shape[0]>32:
            return torch.inf
        return super()._autoquant_test(act_mat, *args)

class AQWeightOnlyQuantizedLinearWeight3(Int8WeightOnlyQuantizedLinearWeight, AQMixin):
    def _quantized_op(act_mat, w_qtensor, bias):
        orig_shape = act_mat.shape
        y = torch.mm(act_mat.reshape(-1, orig_shape[-1]), w_qtensor.int_data*w_qtensor.q_scales)
        y=y.reshape(*orig_shape[:-1], y.shape[-1])
        if bias is not None:
            y += bias
        return y

class AQFloatLinearWeight(torch.Tensor, AQMixin):
    """
    A class to be used in concert with AutoQuantizableLinearWeight to provide a
    default/non-quantized option. Only implements the bare minimum needed to work with the
    AutoQuantizableLinearWeight class using the same interfaces that would normally be
    used by QTensor subclasses but for a default linear op instead.
    """
    def __init__(self):
        super().__init__()

    @staticmethod
    def _quantized_op(act_mat, w_qtensor, bias):
        return torch.nn.functional.linear(act_mat, w_qtensor, bias)

    @classmethod
    def from_float(cls, weight):
        return weight

DEFAULT_CLASS_LIST = [
    AQFloatLinearWeight,
    AQInt8DynamicallyQuantizedLinearWeight,
    AQWeightOnlyQuantizedLinearWeight,
    AQWeightOnlyQuantizedLinearWeight2,
    # AQWeightOnlyQuantizedLinearWeight3,
    # 3rd version gets picked in situations where it is slower for the interpolation mode
]
