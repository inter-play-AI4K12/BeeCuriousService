import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from beecurious_service.config import load_dotenv


class LoadDotenvTest(unittest.TestCase):
    def test_loads_values_without_overwriting_process_environment(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "BEECURIOUS_TEST_VALUE=from-file\n"
                "BEECURIOUS_EXISTING_VALUE=from-file\n",
                encoding="utf-8",
            )
            os.environ["BEECURIOUS_EXISTING_VALUE"] = "from-process"
            try:
                load_dotenv(path)
                self.assertEqual(os.environ["BEECURIOUS_TEST_VALUE"], "from-file")
                self.assertEqual(os.environ["BEECURIOUS_EXISTING_VALUE"], "from-process")
            finally:
                os.environ.pop("BEECURIOUS_TEST_VALUE", None)
                os.environ.pop("BEECURIOUS_EXISTING_VALUE", None)


if __name__ == "__main__":
    unittest.main()
