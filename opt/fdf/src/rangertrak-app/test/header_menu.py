# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# header_menu.py -- [RT_HEADER_MENU_V1] for the page tests: a header button that did not fit (a phone's width) is under ☰,
# as a person reaches it: Page.click on "#<id>Btn" opens ☰ first when the button is in it
import re
from playwright.sync_api import Page
_click = Page.click
def _menu_click(self, selector, *a, **k):
    if re.fullmatch(r"#\w+Btn", selector) and selector != "#menuBtn":
        if self.evaluate("(s)=>{const b=document.querySelector(s); return !!(b && b.closest('#menu') && document.querySelector('#menu').hidden)}", selector):
            _click(self, "#menuBtn")
    return _click(self, selector, *a, **k)
Page.click = _menu_click
