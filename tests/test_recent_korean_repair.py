from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

import discord

from src.bot import NewsCog
from src.core.models import TwitterPost
from src.bot_helpers import (
    _build_layout_view_for_post,
    _twitter_posts_as_news_posts,
    _twitter_status_id_from_message,
    _with_localized_twitter_text,
)


class RecentKoreanRepairTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.cutoff = self.now - timedelta(days=3)
        self.cog = object.__new__(NewsCog)
        self.cog.bot = SimpleNamespace(user=SimpleNamespace(id=1))
        self.cog.config = SimpleNamespace(x_account_username="LimbusCompany_B")
        self.cog.x_source = mock.Mock()
        self.cog.hampang_x_source = mock.Mock()
        self.post = TwitterPost("x:123", "LimbusCompany_B", "https://x.com/LimbusCompany_B/status/123", "English text", "Title", self.now, [], {})
        self.cog._twitter_post_from_stored_id = mock.Mock(return_value=self.post)
        self.cog._localize_twitter_post = mock.AsyncMock(return_value=_with_localized_twitter_text(self.post, "한국어 본문", translation_source="x_translate"))
        embed = discord.Embed(title="Title", description="English text")
        embed.set_image(url="https://example.com/image.png")
        self.message = SimpleNamespace(author=SimpleNamespace(id=1), created_at=self.now, components=[], embeds=[embed], edit=mock.AsyncMock())

    def test_finds_status_id_in_components_v2_link_button(self):
        message = SimpleNamespace(
            content="",
            embeds=[],
            components=[
                SimpleNamespace(
                    content=None,
                    url=None,
                    children=[
                        SimpleNamespace(
                            content=None,
                            url="https://x.com/LimbusCompany_B/status/2101237942136279386",
                            children=[],
                        )
                    ],
                )
            ],
        )
        self.assertEqual(_twitter_status_id_from_message(message), "2101237942136279386")

    async def test_edits_embed_without_changing_images_or_attachments(self):
        self.assertTrue(await self.cog._repair_korean_message(self.message, self.cutoff, "123"))
        kwargs = self.message.edit.call_args.kwargs
        self.assertIn("한국어 본문", kwargs["embeds"][0].description)
        self.assertEqual(kwargs["embeds"][0].image.url, "https://example.com/image.png")
        self.assertNotIn("attachments", kwargs)
        self.message.embeds = kwargs["embeds"]
        self.assertFalse(await self.cog._repair_korean_message(self.message, self.cutoff, "123"))
        self.assertEqual(self.message.edit.await_count, 1)

    async def test_old_message_and_other_author_are_untouched(self):
        self.message.created_at = self.cutoff - timedelta(seconds=1)
        self.assertFalse(await self.cog._repair_korean_message(self.message, self.cutoff, "123"))
        self.message.created_at = self.now
        self.message.author.id = 2
        self.assertFalse(await self.cog._repair_korean_message(self.message, self.cutoff, "123"))
        self.cog._localize_twitter_post.assert_not_awaited()

    async def test_failure_is_retried(self):
        success = self.cog._localize_twitter_post.return_value
        self.cog._localize_twitter_post.side_effect = [self.post, success]
        self.assertFalse(await self.cog._repair_korean_message(self.message, self.cutoff, "123"))
        self.message.edit.assert_not_awaited()
        self.assertTrue(await self.cog._repair_korean_message(self.message, self.cutoff, "123"))

    async def test_components_only_replace_body(self):
        view = _build_layout_view_for_post(_twitter_posts_as_news_posts([self.post], [])[0], include_zip_button=False, include_banner=False)
        self.message.components = [object()]
        self.message.flags = discord.MessageFlags(components_v2=True)
        original_items = list(view.walk_children())
        with mock.patch.object(discord.ui.LayoutView, "from_message", return_value=view):
            self.assertTrue(await self.cog._repair_korean_message(self.message, self.cutoff, "123"))
        self.assertEqual(list(view.walk_children()), original_items)
        self.assertIn("한국어 본문", next(i.content for i in view.walk_children() if isinstance(i, discord.ui.TextDisplay) and i.content.startswith("## ")))
        self.assertNotIn("attachments", self.message.edit.call_args.kwargs)
