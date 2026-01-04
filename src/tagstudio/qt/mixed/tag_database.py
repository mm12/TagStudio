# Copyright (C) 2024 Travis Abendshien (CyanVoxel).
# Licensed under the GPL-3.0 License.
# Created for TagStudio: https://github.com/CyanVoxel/TagStudio


import structlog
import contextlib
from PySide6.QtWidgets import QMessageBox, QPushButton

from tagstudio.core.constants import RESERVED_TAG_END, RESERVED_TAG_START
from tagstudio.core.library.alchemy.library import Library
from tagstudio.core.library.alchemy.models import Tag
from tagstudio.qt.mixed.build_tag import BuildTagPanel
from tagstudio.qt.mixed.tag_search import TagSearchPanel
from tagstudio.qt.translations import Translations
from tagstudio.qt.views.panel_modal import PanelModal

logger = structlog.get_logger(__name__)

# TODO: Once this class is removed, the `is_tag_chooser` option of `TagSearchPanel`
# will most likely be enabled in every case
# and the possibility of disabling it can therefore be removed


class TagDatabasePanel(TagSearchPanel): 
    # TODO: Add counts for tags to the view (on the right side) -> BuildTagPanel?
    # TODO: sort by counts
    def __init__(self, driver, library: Library):
        super().__init__(library, is_tag_chooser=False)
        self.driver = driver

        self.create_tag_button = QPushButton(Translations["tag.create"])
        self.create_tag_button.clicked.connect(lambda: self.build_tag(self.search_field.text()))

        self.root_layout.addWidget(self.create_tag_button)

    def build_tag(self, name: str):
        panel = BuildTagPanel(self.lib)
        self.modal = PanelModal(
            panel,
            Translations["tag.new"],
            has_save=True,
        )
        if name.strip():
            panel.name_field.setText(name)

        self.modal.saved.connect(
            lambda: (
                self.lib.add_tag(
                    tag=panel.build_tag(),
                    parent_ids=panel.parent_ids,
                    alias_names=panel.alias_names,
                    alias_ids=panel.alias_ids,
                ),
                self.modal.hide(),
                self.update_tags(self.search_field.text()),
            )
        )
        self.modal.show()

    def delete_tag(self, tag: Tag):
        if tag.id in range(RESERVED_TAG_START, RESERVED_TAG_END):
            return

        message_box = QMessageBox(
            QMessageBox.Question,  # type: ignore
            Translations["tag.remove"],
            Translations.format("tag.confirm_delete", tag_name=self.lib.tag_display_name(tag)),
            QMessageBox.Ok | QMessageBox.Cancel,  # type: ignore
        )

        result = message_box.exec()

        if result != QMessageBox.Ok:  # type: ignore
            return

        self.lib.remove_tag(tag.id)
        self.update_tags()

    def update_tags(self, query: str | None = None):
        """Update the tag list but sort and prioritize by tag usage counts."""
        logger.info("[TagDatabasePanel] Updating Tags (by count)")

        # Remove the "Create & Add" button if one exists
        if self.create_button_in_layout and self.scroll_layout.count():
            self.scroll_layout.takeAt(self.scroll_layout.count() - 1).widget().deleteLater()
            self.create_button_in_layout = False

        query_lower = "" if not query else query.lower()
        tag_limit = TagSearchPanel.tag_limit if isinstance(TagSearchPanel.tag_limit, int) else -1
        # Fetch all matching tags first, then sort by usage and take the top `tag_limit`.
        # Previously we fetched only the first N alphabetically, then sorted that
        # subset by count which caused many high-count tags to be omitted.
        total_tags = len(self.lib.tags) if hasattr(self.lib, "tags") else -1
        fetch_limit = total_tags if isinstance(total_tags, int) and total_tags > 0 else -1
        tag_results: list[set[Tag]] = self.lib.search_tags(name=query, limit=fetch_limit)
        if self.exclude:
            tag_results[0] = {t for t in tag_results[0] if t.id not in self.exclude}
            tag_results[1] = {t for t in tag_results[1] if t.id not in self.exclude}

        results_0 = list(tag_results[0])
        results_1 = list(tag_results[1])

        # Sort by descending usage count, tie-breaker by name
        results_0.sort(key=lambda tag: ( -self.lib.get_tag_count(tag.id), tag.name.lower() ))
        results_1.sort(key=lambda tag: ( -self.lib.get_tag_count(tag.id), tag.name.lower() ))

        raw_results = list(results_0 + results_1)

        priority_results: set[Tag] = set()
        if query and query.strip():
            for tag in raw_results:
                if tag.name.lower().startswith(query_lower):
                    priority_results.add(tag)

        # Priority results first (sorted by count), then remaining by count
        priority_sorted = sorted(
            list(priority_results), key=lambda tag: (-self.lib.get_tag_count(tag.id), len(tag.name))
        )
        remaining = [r for r in raw_results if r not in priority_results]
        remaining_sorted = sorted(
            remaining, key=lambda tag: (-self.lib.get_tag_count(tag.id), tag.name.lower())
        )
        all_results = priority_sorted + remaining_sorted

        if tag_limit > 0:
            all_results = all_results[:tag_limit]

        if all_results:
            self.first_tag_id = None
            self.first_tag_id = all_results[0].id if len(all_results) > 0 else all_results[0].id
        else:
            self.first_tag_id = None

        norm_previous = self.previous_limit if self.previous_limit > 0 else len(self.lib.tags)
        norm_limit = tag_limit if tag_limit > 0 else len(self.lib.tags)
        range_limit = max(norm_previous, norm_limit)
        for i in range(0, range_limit):
            tag = None
            try:
                tag = all_results[i]
            except Exception:
                tag = None
            self.set_tag_widget(tag=tag, index=i)
        self.previous_limit = tag_limit

        # Add back the "Create & Add" button
        if query and query.strip():
            cb: QPushButton = self.build_create_button(query)
            cb.setText(Translations.format("tag.create_add", query=query))
            with contextlib.suppress(Exception):
                cb.clicked.disconnect()
            cb.clicked.connect(lambda: self.create_and_add_tag(query or ""))
            self.scroll_layout.addWidget(cb)
            self.create_button_in_layout = True
