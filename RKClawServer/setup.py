import os
from pathlib import Path

from setuptools import Distribution, Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from wheel.bdist_wheel import bdist_wheel


CYTHONIZE = os.environ.get("RKCLAW_CYTHONIZE", "0") == "1"
CYTHON_MODULES: set[str] = set()
CYTHON_JOBS = int(os.environ.get(
    "RKCLAW_CYTHON_JOBS",
    str(min(os.cpu_count() or 1, 4)),
))


def cython_extensions():
    if not CYTHONIZE:
        return []

    try:
        from Cython.Build import cythonize
    except ImportError as exc:
        raise RuntimeError(
            "RKCLAW_CYTHONIZE=1 requires Cython; install the release extra first"
        ) from exc

    sources = []
    for source in sorted(Path("gateway").rglob("*.py")):
        # Package initializers and ``python -m gateway`` need Python source.
        # All implementation modules are replaced by extension modules.
        if source.name in {"__init__.py", "__main__.py"}:
            continue
        module = ".".join(source.with_suffix("").parts)
        CYTHON_MODULES.add(module)
        sources.append(Extension(module, [str(source)]))

    return cythonize(
        sources,
        build_dir=os.environ.get("RKCLAW_CYTHON_BUILD_DIR", "build/cython"),
        compiler_directives={
            "language_level": 3,
            "binding": True,
            "emit_code_comments": False,
        },
        nthreads=CYTHON_JOBS,
    )


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


class PlatformWheel(bdist_wheel):
    """Build a platform wheel for the bundled native shared library."""

    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False
        platform = os.environ.get("RKCLAW_WHEEL_PLATFORM")
        if platform:
            self.plat_name = platform

    def get_tag(self):
        python, abi, platform = super().get_tag()
        if CYTHONIZE:
            return python, abi, platform
        return "py3", "none", platform


class CythonBuildPy(build_py):
    """Do not copy Python sources that have Cython extension replacements."""

    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        if not CYTHONIZE:
            return modules
        return [
            item for item in modules
            if f"{item[0]}.{item[1]}" not in CYTHON_MODULES
        ]


class CythonBuildExt(build_ext):
    """Compile independent Cython modules concurrently in release builds."""

    def finalize_options(self):
        super().finalize_options()
        if CYTHONIZE and not self.parallel:
            self.parallel = CYTHON_JOBS


setup(
    distclass=BinaryDistribution,
    cmdclass={
        "bdist_wheel": PlatformWheel,
        "build_ext": CythonBuildExt,
        "build_py": CythonBuildPy,
    },
    ext_modules=cython_extensions(),
    zip_safe=False,
)
