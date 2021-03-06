# coding: utf-8
#
# Copyright 2014 The Oppia Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Controllers for the videioList page."""
import jinja2

from core.controllers import base
from core.controllers import editor
from core.domain import config_domain
from core.domain import gadget_registry
from core.domain import rte_component_registry
from core.domain import video_list_service
from core.domain import video_list_demain
from core.storage.video_list import gae_models as video_list_models
import feconf
import utils


# import utils


class VedioListHandler(base.BaseHandler):
    """处理视频列表"""
    PAGE_NAME_FOR_CSRF = "editor"

    def get(self):
        self.render_json(self.values)


class VideoListPage(base.BaseHandler):
    PAGE_NAME_FOR_CSRF = "editor"
    EDITOR_PAGE_DEPENDENCY_IDS = []

    def get(self, category_id):
        if self.username in config_domain.BANNED_USERNAMES.value:
            raise self.UnauthorizedUserException("")
        else:

            interaction_templates = (
                rte_component_registry.Registry.get_html_for_all_components())

            gadget_types = gadget_registry.Registry.get_all_gadget_types()
            gadget_templates = (
                gadget_registry.Registry.get_gadget_html(gadget_types))
            category = video_list_models.VideoCategory.get(long(category_id))
            self.values.update({
                'meta_description': feconf.SPLASH_PAGE_DESCRIPTION,
                'nav_mode': 'video',
                'value_generators_js': jinja2.utils.Markup(
                    editor.get_value_generators_js()),
                'gadget_templates': jinja2.utils.Markup(gadget_templates),
                'interaction_templates': jinja2.utils.Markup(
                    interaction_templates),
                'category_id': category_id,
                'category_name': category.name,
                'objective': category.objective,
                'thumbnail_icon_url':
                    utils.get_thumbnail_icon_url_for_category(
                        category.category),
                'thumbnail_bg_color':
                    utils.get_hex_color_for_category(category.category)
            })
            self.render_template(
                'video_list/video_list.html')


class VideoListData(base.BaseHandler):
    PAGE_NAME_FOR_CSRF = "editor"

    """视频数据处理"""

    def get(self, category_id, video_id):
        if video_id is not None and video_id != '0':
            video = video_list_service.get_by_id(video_id)
            self.values.update(video.to_dict())
            self.render_json(self.values)
        else:
            urlsafe_start_cursor = self.request.get('cursor')
            lists, new_urlsafe_start_cursor, more = \
                video_list_service.get_all_video(
                    category_id,
                    urlsafe_start_cursor=urlsafe_start_cursor)
            category = video_list_models.VideoCategory.get(long(category_id))
            self.values.update({
                'results': [m.to_dict() for m in lists],
                'cursor': new_urlsafe_start_cursor,
                'more': more,
                'category_objective': category.objective
            })
            self.render_json(self.values)

    def post(self, category_id, video_id):
        name = self.payload.get('name')
        category = category_id
        ids = self.payload.get('ids')
        if video_id is not None and video_id != '0':
            video = video_list_service.get_video_model(video_id)
            video.name = name
            video.category = category
            video.ids = ids
            video.put()
        else:
            video_list_service.create_video(self.user_id, name, category, ids)
        self.render_json(self.values)

    def delete(self, category_id, video_id):
        """删除视频"""
        video_list_service.delete_video(category_id, video_id)


class VideoCategoryList(base.BaseHandler):
    PAGE_NAME_FOR_CSRF = "editor"
    EDITOR_PAGE_DEPENDENCY_IDS = []

    def get(self):
        if self.username in config_domain.BANNED_USERNAMES.value:
            raise self.UnauthorizedUserException("用户被禁止访问")
        else:

            interaction_templates = (
                rte_component_registry.Registry.get_html_for_all_components())

            gadget_types = gadget_registry.Registry.get_all_gadget_types()
            gadget_templates = (
                gadget_registry.Registry.get_gadget_html(gadget_types))

            self.values.update({
                'meta_description': feconf.SPLASH_PAGE_DESCRIPTION,
                'nav_mode': 'video',
                'value_generators_js': jinja2.utils.Markup(
                    editor.get_value_generators_js()),
                'gadget_templates': jinja2.utils.Markup(gadget_templates),
                'interaction_templates': jinja2.utils.Markup(
                    interaction_templates)
            })
            self.render_template(
                'video_list/videoCatelogList.html')


class VideoCategoryData(base.BaseHandler):
    PAGE_NAME_FOR_CSRF = "editor"

    def get(self, category_id):
        if category_id is not None and category_id != '0':
            video_category = video_list_models.VideoCategory.get(
                long(category_id), False)
            if video_category is None:
                raise self.PageNotFoundException
            m = video_category
            self.values.update(video_list_demain.VideoCategoryList(
                m.id, m.name, m.picture_name, m.category,
                m.author_id, m.created_on, m.last_updated,
                m.objective
            ).to_dict())
            self.render_json(self.values)

        else:
            urlsafe_start_cursor = self.request.get('cursor')
            lists, new_urlsafe_start_cursor, more = \
                video_list_service.get_all_video_category(
                    urlsafe_start_cursor=urlsafe_start_cursor)
            self.values.update({
                'results': [video_list_demain.VideoCategoryList(
                    m.id, m.name, m.picture_name, m.category,
                    m.author_id, m.created_on, m.last_updated,
                    m.objective
                ).to_dict() for m in lists],
                'cursor': new_urlsafe_start_cursor,
                'more': more,
            })
            self.render_json(self.values)

    def post(self, category_id):
        name = self.payload.get('name')
        category = self.payload.get('category')
        picture_name = self.payload.get('picture_name')
        objective = self.payload.get('objective')
        if category_id is not None and category_id != '0':
            video = video_list_models.VideoCategory.get(long(category_id))
        else:
            video = video_list_models.VideoCategory()
            video.author_id = self.user_id
        video.name = name
        video.category = category
        video.objective = objective
        video.picture_name = picture_name
        video.put()
        self.render_json(self.values)

    def delete(self, category_id):
        if category_id is not None and category_id != '0':
            video_category = video_list_models.VideoCategory.get(
                long(category_id), False)
            if video_category is None:
                raise self.PageNotFoundException
            else:
                video_category.delete()


class VideoView(base.BaseHandler):
    PAGE_NAME_FOR_CSRF = "view"
    EDITOR_PAGE_DEPENDENCY_IDS = []

    def get(self, video_id):
        if self.username in config_domain.BANNED_USERNAMES.value:
            raise self.UnauthorizedUserException("")
        else:

            interaction_templates = (
                rte_component_registry.Registry.get_html_for_all_components())

            gadget_types = gadget_registry.Registry.get_all_gadget_types()
            gadget_templates = (
                gadget_registry.Registry.get_gadget_html(gadget_types))

            self.values.update({
                'meta_description': feconf.SPLASH_PAGE_DESCRIPTION,
                'nav_mode': 'video',
                'value_generators_js': jinja2.utils.Markup(
                    editor.get_value_generators_js()),
                'gadget_templates': jinja2.utils.Markup(gadget_templates),
                'interaction_templates': jinja2.utils.Markup(
                    interaction_templates)
            })
            if video_id is not None and video_id != '0':
                video = video_list_service.get_by_id(video_id)
                category = video_list_models.VideoCategory.get(
                    long(video.category.strip('"')))
                self.values.update(video.to_dict())
                self.values.update({
                    'category_name': category.name,
                    'category_id': category.id,
                    'category': video.category.strip('"')
                })
                self.render_template("video_list/video.html")
            else:
                raise self.PageNotFoundException(None)
