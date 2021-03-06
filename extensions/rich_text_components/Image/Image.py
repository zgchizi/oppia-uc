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
# Unless required by applicable law or agreed to in writing, softwar
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from extensions.rich_text_components import base


class Image(base.BaseRichTextComponent):
    """A rich-text component representing an inline image."""

    name = 'Image'
    category = 'Basic Input'
    description = '图片.'
    frontend_name = 'image'
    tooltip = '插入图片'
    requires_fs = False
    is_block_element = True

    _customization_arg_specs = [{
        'name': 'filepath',
        'description': (
            '文件名称. (允许文件类型: gif, jpeg, jpg, '
            'png.)'),
        'schema': {
            'type': 'custom',
            'obj_type': 'Filepath',
        },
        'default_value': '',
    }, {
        'name': 'caption',
        'description': ('文件标题 (可选)'),
        'schema': {
            'type': 'unicode',
        },
        'default_value': '',
    }, {
        'name': 'alt',
        'description': '提示文字',
        'schema': {
            'type': 'unicode',
        },
        'default_value': '',
    }]

    @property
    def preview_url_template(self):
        return '/imagehandler/<[explorationId]>/<[filepath]>'
