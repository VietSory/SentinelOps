"""Bundle authoritative contracts and the frozen model config from root sources."""
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
        model_config = Path(__file__).parent / 'configs' / 'alibaba_relative_features.json'
        model_target = Path(self.build_lib) / 'sentinelops' / 'ml' / 'configs'
        model_target.mkdir(parents=True, exist_ok=True)
        self.copy_file(str(model_config), str(model_target / model_config.name))


setup(cmdclass={"build_py": BuildWithContracts},package_data={'sentinelops.ui':['assets/*']})
