"""Bundle the authoritative root contracts without maintaining a second copy."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithContracts(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "sentinelops" / "schemas"
        target.mkdir(parents=True, exist_ok=True)
        for source in sorted((Path(__file__).parent / "contracts").glob("*.schema.json")):
            self.copy_file(str(source), str(target / source.name))


setup(cmdclass={"build_py": BuildWithContracts})
