from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timezone

from src.clients.steam_client import format_steam_news_for_discord
from src.core.models import NewsPost
from src.core.storage import _post_content_hash


SECTION_CAUSE = "\ubc1c\uc0dd \uc6d0\uc778 \ubc0f \ud604\uc7ac\uae4c\uc9c0 \ud655\uc778\ub41c \ub0b4\uc6a9"
SECTION_IMPORTANT = "\ucd94\uac00\ub85c \ud655\uc778\uc774 \ud544\uc694\ud55c \uc0ac\ud56d \ubc0f \uc548\ub0b4 (\uc911\uc694)"
SECTION_CONDITION = "\ubc1c\uc0dd \uc870\uac74"
STEADY_ENVIRONMENT = "\uc548\uc815\uc801\uc778 \uac8c\uc784 \uc2e4\ud589 \ud658\uacbd"
ENGINE_EFFECT = "\uc5d4\uc9c4 \uc5c5\uadf8\ub808\uc774\ub4dc \uc5ec\ud30c"
SPECIFIC_EGO_GIFT = "\ud2b9\uc815 E.G.O \uae30\ud504\ud2b8 \ubcf4\uc720"


class SteamMarkdownTests(unittest.TestCase):
    def test_heading_angle_text_is_preserved_as_component_markdown(self) -> None:
        raw = (
            f"[h3]<{SECTION_CAUSE}>\n[/h3]\n"
            f"1. {ENGINE_EFFECT}\n"
            "- 2026\ub144 5\uc6d4 28\uc77c\uc5d0 \ubc30\ud3ec\ub41c 1.106.0 \uc5c5\ub370\uc774\ud2b8\n\n"
            f"[h3]<{SECTION_IMPORTANT}>\n[/h3]\n"
            "[u](* \ud2b9\ud788 iOS 26.4 \uc774\uc0c1 \ubc84\uc804\uc5d0\uc11c OS \uc790\uccb4\uc758 "
            "\uba54\ubaa8\ub9ac \uc810\uc720\uc728\uc774 \ub192\uc544\uc9c4 \uac83\uc744 \ud655\uc778)[/u]\n\n"
            f"[b]\u203b {STEADY_ENVIRONMENT}\uc744 \uc548\ub0b4\ub4dc\ub9ac\uae30 \uc704\ud574, "
            "\ucd94\ud6c4 \ucd5c\uc18c \uae30\uae30 \uc0ac\uc591\uc774 \uc870\uc815\ub420 \uc608\uc815\uc785\ub2c8\ub2e4.[/b]"
        )

        formatted = format_steam_news_for_discord(raw)

        self.assertIn(f"### **<{SECTION_CAUSE}>**", formatted)
        self.assertIn(f"### **<{SECTION_IMPORTANT}>**", formatted)
        self.assertIn("__(* \ud2b9\ud788 iOS 26.4 \uc774\uc0c1 \ubc84\uc804", formatted)
        self.assertIn(f"**\u203b {STEADY_ENVIRONMENT}", formatted)
        self.assertNotIn("****", formatted)

    def test_plain_angle_section_titles_are_not_stripped_as_html(self) -> None:
        formatted = format_steam_news_for_discord(
            f"<{SECTION_CONDITION}>\n- {SPECIFIC_EGO_GIFT}"
        )

        self.assertIn(f"<{SECTION_CONDITION}>", formatted)

    def test_steam_content_hash_changes_from_legacy_renderer_hash(self) -> None:
        post = NewsPost(
            post_id="steam:koreana:669495150925323005",
            source_user="Limbus Company Steam News",
            url="https://store.steampowered.com/news/app/1973530/view/669495150925323005",
            text=f"### **<{SECTION_CAUSE}>**",
            title=(
                "2026\ub144 5\uc6d4 28\uc77c Unity \ubc84\uc804 \uc5c5\uadf8\ub808\uc774\ub4dc "
                "\uc774\ud6c4 \ubc1c\uc0dd\ud55c \uc811\uc18d \ubc0f \uc9c4\ud589 \ubd88\uac00 \ud604\uc0c1"
            ),
            created_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
            image_urls=[],
            raw={"source": "steam_initial_events", "language": "koreana"},
        )
        legacy_raw = (
            f"{post.title}\x00{post.text}\x00"
            f"{json.dumps(post.image_urls, sort_keys=True)}"
        )
        legacy_hash = hashlib.sha256(legacy_raw.encode()).hexdigest()[:16]

        self.assertNotEqual(_post_content_hash(post), legacy_hash)


if __name__ == "__main__":
    unittest.main()
