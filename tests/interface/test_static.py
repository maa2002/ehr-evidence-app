"""Checks of the page's own files (interface/static).

The page is plain HTML, CSS and JavaScript without a build step, served
under a content security policy that admits its own files only. These
checks keep it so: no inline script, style or handler; no address of
another site; nothing parsed as markup from a string; and every element
and icon the script names is in the page.
"""

import os
import re
import unittest

from ehr_evidence_app.interface import app as interface

STATIC = interface.STATIC_DIR


def read(name):
    """Return one of the page's files."""
    with open(os.path.join(STATIC, name), "r", encoding="utf-8") as handle:
        return handle.read()


class StaticTests(unittest.TestCase):
    """The page, its script, its style sheet and its icon."""

    def test_files(self):
        """The page and exactly its named files are served."""
        self.assertEqual(sorted(os.listdir(STATIC)),
                         sorted(interface.ASSETS + (interface.PAGE,)))
        page = read(interface.PAGE)
        for name in interface.ASSETS:
            self.assertIn("/static/%s?v={{version}}" % name, page)

    def test_nothing_inline(self):
        """The page has no inline script, style or event handler, so that
        the policy need admit none."""
        page = read(interface.PAGE)
        for script in re.findall(r"<script\b[^>]*>(.*?)</script>", page,
                                 flags=re.S):
            self.assertEqual(script.strip(), "")
        self.assertNotRegex(page, r"<style\b")
        self.assertNotRegex(page, r"\sstyle\s*=")
        self.assertNotRegex(page, r"\son[a-z]+\s*=")

    def test_no_other_site(self):
        """No file names an address of another site; the SVG namespace
        is a name, not an address fetched."""
        for name in interface.ASSETS + (interface.PAGE,):
            text = read(name).replace("http://www.w3.org/2000/svg", "")
            self.assertNotRegex(text, r"https?://", name)
            self.assertNotRegex(text, r"@import|url\(", name)

    def test_script_builds_text_only(self):
        """The script never parses a string as markup or code."""
        script = read("app.js")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                     "document.write", "eval(", "new Function",
                     "setAttribute(\"style\"", "srcdoc"):
            self.assertNotIn(sink, script, sink)

    def test_named_elements_and_icons_exist(self):
        """Every element the script looks up, and every icon the page and
        the script use, is in the page."""
        page, script = read(interface.PAGE), read("app.js")
        ids = set(re.findall(r'\bid="([^"]+)"', page))
        wanted = set(re.findall(r'byId\("([^"]+)"\)', script))
        self.assertTrue(wanted)
        self.assertLessEqual(wanted, ids)
        symbols = {name[2:] for name in ids if name.startswith("i-")}
        used = set(re.findall(r'href="#i-([a-z-]+)"', page)) | set(
            re.findall(r'icon\("([a-z-]+)"', script))
        self.assertTrue(used)
        self.assertLessEqual(used, symbols)


if __name__ == "__main__":
    unittest.main()
