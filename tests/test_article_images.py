"""Guard the checked-in library against regressions in Kobo image handling."""
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse, unquote

from bs4 import BeautifulSoup
from PIL import Image


class PublishedImageTests(unittest.TestCase):
    def test_every_article_image_is_a_local_baseline_rgb_jpeg(self):
        root = Path(__file__).resolve().parents[1]
        specs = json.loads((root / 'articles.json').read_text())['articles']
        pages = [root / 'articles' / spec['slug'] / 'index.html'
                 for spec in specs if spec['source_type'] == 'arxiv']
        self.assertTrue(pages)
        for page in pages:
            soup = BeautifulSoup(page.read_text(), 'html.parser')
            for img in soup.find_all('img'):
                with self.subTest(page=page.parent.name, src=img.get('src')):
                    src = img.get('src', '')
                    self.assertTrue(src)
                    url = urlparse(src)
                    self.assertEqual(url.scheme, 'https')
                    self.assertEqual(url.netloc, 'theopinard.github.io')
                    prefix = f'/vrac/articles/{page.parent.name}/'
                    self.assertTrue(url.path.startswith(prefix))
                    name = unquote(url.path[len(prefix):])
                    self.assertEqual(Path(name).name, name)
                    asset = page.parent / name
                    self.assertTrue(asset.is_file())
                    with Image.open(asset) as image:
                        image.load()
                        self.assertEqual(image.format, 'JPEG')
                        self.assertEqual(image.mode, 'RGB')
                        self.assertFalse(image.info.get('progressive'))
                    self.assertFalse(img.get('srcset'))
