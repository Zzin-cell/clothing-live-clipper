import ctranslate2

print("ctranslate2", getattr(ctranslate2, "__version__", "?"))
try:
    n = ctranslate2.get_cuda_device_count()
except Exception as e:
    n = f"err:{e}"
print("cuda_devices", n)

try:
    import torch

    print("torch", torch.__version__, "cuda", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("gpu", torch.cuda.get_device_name(0))
except Exception as e:
    print("torch_err", e)
