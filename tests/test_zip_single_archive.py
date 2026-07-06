from __future__ import annotations

import io
import unittest
import zipfile

import numpy as np
from PIL import Image

from src.bot import NewsCog


def noisy_png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, size=(512, 512, 3), dtype=np.uint8)
    image = Image.fromarray(data, mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ZipSingleArchiveTests(unittest.TestCase):
    def test_optimized_zip_keeps_all_images_in_one_upload_sized_archive(self) -> None:
        items = [(f"image_{index}.png", noisy_png(index)) for index in range(5)]
        max_bytes = 650 * 1024

        native_buffer = NewsCog._zip_buffer_for_items(items)
        optimized_buffer = NewsCog._optimized_zip_buffer_for_items(items, max_bytes)

        self.assertGreater(native_buffer.getbuffer().nbytes, max_bytes)
        self.assertIsNotNone(optimized_buffer)
        assert optimized_buffer is not None
        self.assertLessEqual(optimized_buffer.getbuffer().nbytes, max_bytes)

        with zipfile.ZipFile(optimized_buffer) as archive:
            names = archive.namelist()

        self.assertEqual(len(names), len(items))
        self.assertTrue(all(name.endswith(".jpg") for name in names))


if __name__ == "__main__":
    unittest.main()
