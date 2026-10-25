"""Exercise wallpaper cover geometry without a running Wayland compositor."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def renderer_module():
    gi = ModuleType("gi")
    gi.require_version = Mock()
    repository = ModuleType("gi.repository")
    repository.Gtk = SimpleNamespace(Image=object)
    repository.Gdk = Mock()
    repository.GdkPixbuf = Mock()
    repository.GLib = Mock()
    repository.GtkLayerShell = Mock()
    spec = importlib.util.spec_from_file_location("wallpaper_under_test", Path(__file__).resolve().parents[1] / "wallpaper.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        spec.loader.exec_module(module)
    return module


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.module = renderer_module()
        self.image = Mock()
        self.image._source = Mock()
        self.image._source.get_width.return_value = 1920
        self.image._source.get_height.return_value = 1080
        self.image._scaled_size = None

    def update(self, width, height):
        self.module.Wallpaper._update_image(self.image, width, height)

    def test_portrait_center_crop_and_resize(self):
        self.update(360, 640)
        self.image._source.scale_simple.assert_called_once_with(
            1138, 640, self.module.GdkPixbuf.InterpType.BILINEAR)
        self.module.GdkPixbuf.Pixbuf.new_subpixbuf.assert_called_once_with(
            self.image._source.scale_simple.return_value, 389, 0, 360, 640)
        self.image.set_from_pixbuf.assert_called_once()
        self.update(640, 360)
        self.assertEqual(self.image._source.scale_simple.call_count, 2)
        self.assertEqual(self.image._scaled_size, (640, 360))

    def test_same_allocation_is_cached(self):
        self.update(800, 600)
        self.update(800, 600)
        self.image._source.scale_simple.assert_called_once()

    def test_disabled_or_zero_size_does_not_scale(self):
        self.update(0, 640)
        self.update(360, 0)
        self.image._source.scale_simple.assert_not_called()
        self.image._source = None
        self.update(360, 640)
        self.image.set_from_pixbuf.assert_not_called()

    def test_wallpaper_does_not_request_image_dimensions(self):
        self.assertEqual(self.module.Wallpaper.do_get_preferred_width(self.image), (0, 0))
        self.assertEqual(self.module.Wallpaper.do_get_preferred_height(self.image), (0, 0))


if __name__ == "__main__":
    unittest.main()
