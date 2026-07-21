from pathlib import Path
import site

sp = Path(site.getusersitepackages())
print("user site", sp)
print("names", [p.name for p in sp.glob("nvidia*")][:50] if sp.exists() else None)

# store site
sp2 = Path(r"C:\Users\MR\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages")
print("store site", sp2, sp2.exists())
if sp2.exists():
    print([p.name for p in sp2.glob("nvidia*")][:50])
    for dll in sp2.rglob("cublas64_*.dll"):
        print("DLL", dll)
