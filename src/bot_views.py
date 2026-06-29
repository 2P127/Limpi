from __future__ import annotations

import asyncio

import discord

from .bot_constants import (
    BRIGHTEN_CUSTOM_ID_PREFIX,
    EGO_GIFT_SELECT_PAGE_SIZE,
    HAMPANG_SOURCE_X,
    NEWS_SELECT_PAGE_SIZE,
    ZIP_CUSTOM_ID_PREFIX,
)
from .bot_helpers import (
    EgoGift,
    _choice_name,
    _ego_gift_component_markdown,
    _ego_gift_grade_label,
    _ego_gift_keyword,
    _ego_gift_keyword_counts,
    _filter_ego_gifts,
    _hampang_choice_description,
    _hampang_choice_name,
    _news_ui_text,
    _truncate_component_text,
)
from .clients.youtube_client import YoutubeUpload
from .core.models import NewsPost, TwitterPost


def _has_news_cog_method(cog: object, name: str) -> bool:
    return callable(getattr(cog, name, None))


class ZipDownloadButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"limpi:zip:(?P<post_id>.+)",
):
    def __init__(self, post_id: str, *, language: str = "koreana") -> None:
        super().__init__(
            discord.ui.Button(
                label=_news_ui_text(language, "download_images"),
                style=discord.ButtonStyle.primary,
                custom_id=f"{ZIP_CUSTOM_ID_PREFIX}{post_id}",
                emoji="🗂️",
            )
        )
        self.post_id = post_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["post_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("NewsCog")
        if not _has_news_cog_method(cog, "handle_zip_request"):
            await interaction.response.send_message(
                _news_ui_text("koreana", "zip_unavailable"), ephemeral=True
            )
            return
        await cog.handle_zip_request(interaction, self.post_id)


class BrightenSpoilerButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"limpi:brighten:(?P<post_id>.+):(?P<index>\d+)",
):
    def __init__(
        self, post_id: str, *, image_index: int = 0, language: str = "koreana"
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label="밝기 올리기",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{BRIGHTEN_CUSTOM_ID_PREFIX}{post_id}:{image_index}",
                emoji="🔆",
            )
        )
        self.post_id = post_id
        self.image_index = image_index
        self.language = language

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["post_id"], image_index=int(match["index"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.send_messages:
            await interaction.response.send_message(
                "이 채널에서는 밝기 올리기를 사용할 수 없어요. 당신의 권한을 확인해주세요.", ephemeral=True
            )
            return
        cog = interaction.client.get_cog("NewsCog")
        if not _has_news_cog_method(cog, "prompt_brighten_spoiler_visibility"):
            await interaction.response.send_message(
                "지금은 이미지를 처리할 수 없어요.", ephemeral=True
            )
            return
        await cog.prompt_brighten_spoiler_visibility(
            interaction,
            self.post_id,
            image_index=self.image_index,
        )


class BrightenSpoilerVisibilityView(discord.ui.View):
    def __init__(self, author_id: int, post_id: str, image_index: int) -> None:
        super().__init__(timeout=30)
        self.author_id = author_id
        self.post_id = post_id
        self.image_index = image_index

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True

        await interaction.response.send_message(
            "이 선택 버튼은 처음 누른 사람만 사용할 수 있어요.",
            ephemeral=True,
        )
        return False

    async def _send_result(self, interaction: discord.Interaction, *, ephemeral: bool) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="이미지를 처리하고 있어요.",
            embed=None,
            view=self,
        )
        cog = interaction.client.get_cog("NewsCog")
        if not _has_news_cog_method(cog, "_get_brightened_image"):
            await interaction.followup.send(
                "지금은 이미지를 처리할 수 없어요.",
                ephemeral=True,
            )
            return
        await cog.handle_brighten_spoiler_request(
            interaction,
            self.post_id,
            image_index=self.image_index,
            ephemeral=ephemeral,
        )

    @discord.ui.button(label="나만 보기", style=discord.ButtonStyle.primary)
    async def private_result(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._send_result(interaction, ephemeral=True)

    @discord.ui.button(label="채널에 보내기", style=discord.ButtonStyle.danger)
    async def public_result(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._send_result(interaction, ephemeral=False)


class NewsPostSelect(discord.ui.Select):
    def __init__(self, parent: "NewsPostSelectView") -> None:
        options = parent.current_options()
        super().__init__(
            placeholder="게시물을 선택해주세요.",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not options,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.handle_select(interaction, self.values[0])


class NewsPostSelectView(discord.ui.View):
    def __init__(
        self,
        cog: "NewsCog",
        author_id: int,
        posts: list[NewsPost],
        *,
        mode: str,
        source_mode: str,
        language: str,
        private: bool = True,
        attach_photos: bool = True,
        channel_id: int | None = None,
        role_id: int | None = None,
    ) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.posts = posts
        self.mode = mode
        self.source_mode = source_mode
        self.language = language
        self.private = private
        self.attach_photos = attach_photos
        self.channel_id = channel_id
        self.role_id = role_id
        self.page = 0
        self.refresh_items()

    @property
    def max_page(self) -> int:
        if not self.posts:
            return 0
        return (len(self.posts) - 1) // NEWS_SELECT_PAGE_SIZE

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True

        await interaction.response.send_message(
            "이 선택 메뉴는 명령어를 실행한 사람만 사용할 수 있어요.",
            ephemeral=True,
        )
        return False

    def current_options(self) -> list[discord.SelectOption]:
        start = self.page * NEWS_SELECT_PAGE_SIZE
        page_posts = self.posts[start:start + NEWS_SELECT_PAGE_SIZE]
        options: list[discord.SelectOption] = []
        for post in page_posts:
            label = _choice_name(post, include_language=False, include_source=False)
            description = _choice_name(post, include_language=True, include_source=True)
            index = start + len(options)
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(index),
                    description=description[:100],
                )
            )
        return options

    def refresh_items(self) -> None:
        self.clear_items()
        self.add_item(NewsPostSelect(self))
        prev_button = discord.ui.Button(
            label="이전",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
        )
        next_button = discord.ui.Button(
            label="다음",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.max_page,
        )
        prev_button.callback = self.previous_page
        next_button.callback = self.next_page
        self.add_item(prev_button)
        self.add_item(next_button)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
        self.refresh_items()
        await self.update_message(interaction)

    async def next_page(self, interaction: discord.Interaction) -> None:
        if self.page < self.max_page:
            self.page += 1
        self.refresh_items()
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def build_embed(self) -> discord.Embed:
        title = "게시물을 선택해주세요"
        if self.mode == "send":
            title = "보낼 게시물을 선택해주세요"
        description = f"{self.page + 1} / {self.max_page + 1} 페이지"
        if not self.posts:
            description = "선택할 수 있는 게시물이 없어요."
        return discord.Embed(
            title=title,
            description=description,
            color=discord.Color.from_rgb(179, 28, 28),
        )

    async def handle_select(self, interaction: discord.Interaction, value: str) -> None:
        try:
            post = self.posts[int(value)]
        except (IndexError, ValueError):
            await interaction.response.send_message(
                "선택한 게시물을 찾지 못했어요.",
                ephemeral=True,
            )
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="선택한 게시물을 처리하고 있어요.",
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if self.mode == "send":
            await self.cog._send_news_by_selected_post(
                interaction,
                post.post_id,
                source_mode=self.source_mode,
                channel_id=self.channel_id,
                role_id=self.role_id,
            )
            return

        await self.cog._show_previous_news_by_selected_post(
            interaction,
            post.post_id,
            source_mode=self.source_mode,
            language=self.language,
            private=self.private,
            attach_photos=self.attach_photos,
        )


class HampangNewsSelect(discord.ui.Select):
    def __init__(self, parent: "HampangNewsSelectView") -> None:
        options = parent.current_options()
        super().__init__(
            placeholder="햄햄팡팡 소식을 선택해주세요.",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not options,
            custom_id="limpi:hampang:select",
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.handle_select(interaction, self.values[0])


class HampangNewsSelectView(discord.ui.View):
    def __init__(
        self,
        cog: "NewsCog",
        author_id: int,
        items: list[tuple[str, TwitterPost | YoutubeUpload]],
        *,
        mode: str,
        private: bool = True,
        channel_id: int | None = None,
        role_id: int | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.author_id = author_id
        self.items = items
        self.mode = mode
        self.private = private
        self.channel_id = channel_id
        self.role_id = role_id
        self.page = 0
        self.refresh_items()

    @property
    def max_page(self) -> int:
        if not self.items:
            return 0
        return (len(self.items) - 1) // NEWS_SELECT_PAGE_SIZE

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "이 선택 메뉴는 명령어를 실행한 사람만 사용할 수 있어요.",
            ephemeral=True,
        )
        return False

    def current_options(self) -> list[discord.SelectOption]:
        start = self.page * NEWS_SELECT_PAGE_SIZE
        page_items = self.items[start:start + NEWS_SELECT_PAGE_SIZE]
        options: list[discord.SelectOption] = []
        for source, item in page_items:
            index = start + len(options)
            options.append(
                discord.SelectOption(
                    label=_hampang_choice_name(source, item)[:100],
                    value=str(index),
                    description=_hampang_choice_description(source, item)[:100],
                )
            )
        return options

    def refresh_items(self) -> None:
        self.clear_items()
        self.add_item(HampangNewsSelect(self))
        prev_button = discord.ui.Button(
            label="이전",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            custom_id="limpi:hampang:previous",
        )
        next_button = discord.ui.Button(
            label="다음",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.max_page,
            custom_id="limpi:hampang:next",
        )
        prev_button.callback = self.previous_page
        next_button.callback = self.next_page
        self.add_item(prev_button)
        self.add_item(next_button)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
        self.refresh_items()
        await self.update_message(interaction)

    async def next_page(self, interaction: discord.Interaction) -> None:
        if self.page < self.max_page:
            self.page += 1
        self.refresh_items()
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def build_embed(self) -> discord.Embed:
        title = (
            "보낼 햄햄팡팡 소식을 선택해주세요"
            if self.mode == "send"
            else "확인할 햄햄팡팡 이전 소식을 선택해주세요"
        )
        x_count = sum(1 for source, _ in self.items if source == HAMPANG_SOURCE_X)
        youtube_count = len(self.items) - x_count
        description = (
            f"{self.page + 1} / {self.max_page + 1} 페이지"
            f"\nX(트위터) {x_count}개 · YouTube {youtube_count}개"
        )
        return discord.Embed(
            title=title,
            description=description,
            color=discord.Color.from_rgb(179, 28, 28),
        )

    async def handle_select(self, interaction: discord.Interaction, value: str) -> None:
        try:
            source, item = self.items[int(value)]
        except (IndexError, ValueError):
            await interaction.response.send_message(
                "선택한 햄햄팡팡 소식을 찾지 못했어요.",
                ephemeral=True,
            )
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="선택한 햄햄팡팡 소식을 처리하고 있어요.",
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if self.mode == "send":
            await self.cog._send_selected_hampang_news(
                interaction,
                source,
                item,
                channel_id=self.channel_id,
                role_id=self.role_id,
            )
            return
        await self.cog._show_selected_hampang_news(
            interaction,
            source,
            item,
            private=self.private,
        )


class EgoGiftSearchModal(discord.ui.Modal, title="에고 기프트 검색"):
    query = discord.ui.TextInput(
        label="검색어",
        placeholder="예: 재에서 재로, 화상, 배터리",
        required=False,
        max_length=100,
    )

    def __init__(self, parent: "EgoGiftSelectView") -> None:
        super().__init__()
        self.parent_view = parent
        self.query.default = parent.query

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.query = str(self.query.value).strip()
        self.parent_view.keyword = None
        self.parent_view.page = 0
        self.parent_view.selected_gift = None
        self.parent_view.refresh_items()
        await self.parent_view.update_message(interaction)


class EgoGiftKeywordSelect(discord.ui.Select):
    def __init__(self, parent: "EgoGiftSelectView") -> None:
        options = parent.keyword_options()
        super().__init__(
            placeholder="키워드별로 보기",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="limpi:ego-gift:keyword",
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        self.parent_view.keyword = None if value == "__all__" else value
        self.parent_view.page = 0
        self.parent_view.selected_gift = None
        self.parent_view.refresh_items()
        await self.parent_view.update_message(interaction)


class EgoGiftSelect(discord.ui.Select):
    def __init__(self, parent: "EgoGiftSelectView") -> None:
        options = parent.current_options()
        super().__init__(
            placeholder="에고 기프트를 선택해주세요.",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not parent.has_active_filter or not parent.filtered_gifts,
            custom_id="limpi:ego-gift:select",
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.parent_view.handle_select(interaction, self.values[0])


class EgoGiftSelectView(discord.ui.LayoutView):
    def __init__(
        self,
        cog: "NewsCog",
        author_id: int,
        *,
        query: str = "",
        private: bool = True,
    ) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.author_id = author_id
        self.query = query.strip()
        self.private = private
        self.keyword: str | None = None
        self.page = 0
        self.selected_gift: EgoGift | None = None
        self._update_lock = asyncio.Lock()
        self.refresh_items()

    @property
    def filtered_gifts(self) -> list["EgoGift"]:
        return _filter_ego_gifts(self.query, keyword=self.keyword)

    @property
    def has_active_filter(self) -> bool:
        return bool(self.query) or self.keyword is not None

    @property
    def max_page(self) -> int:
        gifts = self.filtered_gifts
        if not gifts:
            return 0
        return (len(gifts) - 1) // EGO_GIFT_SELECT_PAGE_SIZE

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True

        await interaction.response.send_message(
            "이 검색 메뉴는 명령어를 실행한 사람만 사용할 수 있어요.",
            ephemeral=True,
        )
        return False

    def keyword_options(self) -> list[discord.SelectOption]:
        options = [
            discord.SelectOption(
                label="전체 키워드",
                value="__all__",
                default=self.keyword is None,
            )
        ]
        for keyword, count in _ego_gift_keyword_counts():
            options.append(
                discord.SelectOption(
                    label=keyword[:100],
                    value=keyword[:100],
                    description=f"{count}개",
                    default=self.keyword == keyword,
                )
            )
        return options[:25]

    def current_options(self) -> list[discord.SelectOption]:
        if not self.has_active_filter:
            return [
                discord.SelectOption(
                    label="검색 또는 키워드를 선택해주세요",
                    value="__idle__",
                    description="아래 검색 버튼이나 키워드 메뉴를 이용해주세요.",
                )
            ]

        gifts = self.filtered_gifts
        if not gifts:
            return [
                discord.SelectOption(
                    label="검색 결과 없음",
                    value="__none__",
                    description="검색어 또는 키워드를 바꿔주세요.",
                )
            ]

        start = self.page * EGO_GIFT_SELECT_PAGE_SIZE
        page_gifts = gifts[start:start + EGO_GIFT_SELECT_PAGE_SIZE]
        options: list[discord.SelectOption] = []
        for index, gift in enumerate(page_gifts, start=start):
            description = (
                f"{_ego_gift_keyword(gift) or '키워드 없음'} · "
                f"{_ego_gift_grade_label(gift.grade)}"
            )
            if gift.category:
                description += f" · {gift.category}"
            options.append(
                discord.SelectOption(
                    label=gift.name[:100],
                    value=str(index),
                    description=description[:100],
                    default=self.selected_gift == gift,
                )
            )
        return options

    def refresh_items(self, *, image_filename: str | None = None) -> None:
        self.clear_items()
        if self.page > self.max_page:
            self.page = self.max_page

        container = discord.ui.Container(accent_color=discord.Color.from_rgb(179, 28, 28))
        if self.selected_gift is not None:
            if image_filename:
                gallery = discord.ui.MediaGallery()
                gallery.add_item(media=f"attachment://{image_filename}")
                container.add_item(gallery)
                container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    _truncate_component_text(
                        _ego_gift_component_markdown(
                            self.selected_gift,
                            status_text=self.status_text(),
                        ),
                        4000,
                    )
                )
            )
        elif not self.has_active_filter:
            container.add_item(
                discord.ui.TextDisplay(
                    "## **에고 기프트 검색**\n"
                    "에고 기프트 검색을 위해 밑에서 선택해주세요!\n\n"
                    f"-# {self.status_text()}"
                )
            )
        else:
            container.add_item(
                discord.ui.TextDisplay(
                    _truncate_component_text(
                        self._gift_list_markdown(),
                        4000,
                    )
                )
            )

        container.add_item(discord.ui.Separator())

        keyword_row = discord.ui.ActionRow()
        keyword_row.add_item(EgoGiftKeywordSelect(self))
        container.add_item(keyword_row)

        gift_row = discord.ui.ActionRow()
        gift_row.add_item(EgoGiftSelect(self))
        container.add_item(gift_row)

        search_button = discord.ui.Button(
            label="검색",
            style=discord.ButtonStyle.primary,
            custom_id="limpi:ego-gift:search",
        )
        reset_button = discord.ui.Button(
            label="초기화",
            style=discord.ButtonStyle.secondary,
            disabled=not self.query and self.keyword is None,
            custom_id="limpi:ego-gift:reset",
        )
        prev_button = discord.ui.Button(
            label="이전",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            custom_id="limpi:ego-gift:previous",
        )
        next_button = discord.ui.Button(
            label="다음",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.max_page,
            custom_id="limpi:ego-gift:next",
        )
        search_button.callback = self.open_search
        reset_button.callback = self.reset_filters
        prev_button.callback = self.previous_page
        next_button.callback = self.next_page
        action_row = discord.ui.ActionRow()
        action_row.add_item(search_button)
        action_row.add_item(reset_button)
        action_row.add_item(prev_button)
        action_row.add_item(next_button)
        container.add_item(action_row)

        self.add_item(container)

    async def open_search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EgoGiftSearchModal(self))

    async def reset_filters(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.keyword = None
        self.page = 0
        self.selected_gift = None
        self.refresh_items()
        await self.update_message(interaction)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
        self.selected_gift = None
        self.refresh_items()
        await self.update_message(interaction)

    async def next_page(self, interaction: discord.Interaction) -> None:
        if self.page < self.max_page:
            self.page += 1
        self.selected_gift = None
        self.refresh_items()
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction) -> None:
        # build_response가 namu.wiki 이미지를 다운로드할 수 있어 3초를 넘길 수 있다.
        # 먼저 defer로 인터랙션을 ack해 토큰 만료(10062 Unknown interaction)를 막고,
        # 이후 15분 창을 가진 webhook(edit_original_response)으로 편집한다.
        if not interaction.response.is_done():
            await interaction.response.defer()
        async with self._update_lock:
            attachments = await self.build_response()
            await interaction.edit_original_response(
                attachments=attachments,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def build_response(self) -> list[discord.File]:
        file = None
        if self.selected_gift is not None:
            file = await self.cog._ego_gift_image_file(self.selected_gift)
        self.refresh_items(image_filename=file.filename if file is not None else None)
        if self.has_active_filter:
            start = self.page * EGO_GIFT_SELECT_PAGE_SIZE
            self.cog._schedule_ego_gift_image_warmup(
                self.filtered_gifts[start:start + EGO_GIFT_SELECT_PAGE_SIZE]
            )
        return [file] if file is not None else []

    def _gift_list_markdown(self) -> str:
        gifts = self.filtered_gifts
        start = self.page * EGO_GIFT_SELECT_PAGE_SIZE
        page_gifts = gifts[start:start + EGO_GIFT_SELECT_PAGE_SIZE]
        if page_gifts:
            lines = [
                f"`{index + 1}.` **{gift.name}** · {_ego_gift_keyword(gift) or '-'} · "
                f"{_ego_gift_grade_label(gift.grade)}"
                for index, gift in enumerate(page_gifts, start=start)
            ]
            body = "\n".join(lines)
        else:
            body = "검색 결과가 없어요. 검색어를 바꾸거나 키워드를 전체로 돌려보세요."

        return (
            "## **에고 기프트 검색**\n"
            f"{body}\n\n"
            f"-# {self.status_text()}"
        )

    def status_text(self) -> str:
        gifts = self.filtered_gifts
        keyword = self.keyword or "전체"
        query = self.query or "없음"
        return (
            f"검색어: {query} · 키워드: {keyword} · "
            f"{self.page + 1}/{self.max_page + 1} 페이지 · 결과 {len(gifts)}개 · "
            "출처: 나무위키 참고"
        )

    async def handle_select(self, interaction: discord.Interaction, value: str) -> None:
        if value in {"__idle__", "__none__"}:
            await interaction.response.send_message(
                "선택할 에고 기프트가 없어요.",
                ephemeral=True,
            )
            return
        try:
            self.selected_gift = self.filtered_gifts[int(value)]
        except (IndexError, ValueError):
            await interaction.response.send_message(
                "선택한 에고 기프트를 찾지 못했어요.",
                ephemeral=True,
            )
            return
        self.refresh_items()
        await self.update_message(interaction)


class ExternalNewsSendConfirmView(discord.ui.View):
    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True

        await interaction.response.send_message(
            "이 확인 버튼은 명령어를 실행한 사람만 누를 수 있어요.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="네!", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="아니요...생각해볼깨요", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.confirmed = False
        await interaction.response.defer()
        self.stop()

__all__ = [
    "ZipDownloadButton",
    "BrightenSpoilerButton",
    "BrightenSpoilerVisibilityView",
    "NewsPostSelect",
    "NewsPostSelectView",
    "HampangNewsSelect",
    "HampangNewsSelectView",
    "EgoGiftSearchModal",
    "EgoGiftKeywordSelect",
    "EgoGiftSelect",
    "EgoGiftSelectView",
    "ExternalNewsSendConfirmView",
]
