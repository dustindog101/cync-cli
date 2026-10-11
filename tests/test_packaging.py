from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_metadata_and_entry_points_are_present(self) -> None:
        metadata = (ROOT / "pyproject.toml").read_text()
        self.assertIn('name = "cync-cli"', metadata)
        self.assertIn('requires-python = ">=3.10"', metadata)
        self.assertIn('cync = "cync_cli:main"', metadata)
        self.assertTrue((ROOT / "src/cync_cli/__main__.py").is_file())

    def test_installer_avoids_package_installs(self) -> None:
        installer = (ROOT / "install.sh").read_text()
        self.assertIn("--without-pip", installer)
        self.assertNotIn("pip install", installer)
        self.assertIn("Application Support/cync-cli", installer)
        self.assertIn("${CYNC_HOME:-", installer)
        self.assertIn("share/cync-cli/native/main.swift", installer)
        self.assertIn(".local/bin", installer)

    def test_privacy_audit_flags_private_files_and_secrets(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "privacy_audit", ROOT / "scripts/privacy_audit.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text("{}")
            findings = module.audit(root)
            self.assertTrue(any("private-data filename" in item for item in findings))

            (root / "config.json").unlink()
            (root / "source.py").write_text(
                "api" + "_key = '" + "abcdefghijklmnopqrstuvwxyz" + "123456'\n"
            )
            findings = module.audit(root)
            self.assertTrue(any("credential-like value" in item for item in findings))


if __name__ == "__main__":
    unittest.main()
