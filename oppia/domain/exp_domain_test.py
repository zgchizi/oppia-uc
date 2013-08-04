# coding: utf-8
#
# Copyright 2013 Google Inc. All Rights Reserved.
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

__author__ = 'Sean Lip'

import test_utils

from oppia.domain import exp_domain
from oppia.domain import exp_services
import utils


class FakeExploration(exp_domain.Exploration):
    """Allows dummy explorations to be created and commited."""

    def __init__(self, exp_id='fake_exploration_id', owner_id=None):
        """Creates a dummy exploration."""
        self.id = exp_id
        self.title = 'title'
        self.category = 'category'
        self.states = []
        self.parameters = []
        self.is_public = False
        self.image_id = 'image_id'
        self.editor_ids = [owner_id] if owner_id else []

    def put(self):
        """The put() method is patched to make no commits to the datastore."""
        self._pre_put_hook()


class ExplorationDomainUnitTests(test_utils.AppEngineTestBase):
    """Test the exploration domain object."""

    def test_validation(self):
        """Test validation of explorations."""
        exploration = FakeExploration()

        # The 'state_ids property must be a non-empty list of strings
        # representing State ids.
        exploration.states = []
        with self.assertRaisesRegexp(
                utils.ValidationError, 'exploration has no states'):
            exp_services.save_exploration(exploration)
        exploration.states = [
            exp_domain.State('A string', 'name', [], [], None)]
        with self.assertRaisesRegexp(
                utils.ValidationError, 'Invalid state_id'):
            exp_services.save_exploration(exploration)

        new_state = exp_domain.State(
            'Initial state id', 'name', [], [], None)
        exp_services.save_state(new_state)
        exploration.states = [new_state]

        # There must be at least one editor id.
        exploration.editor_ids = []
        with self.assertRaisesRegexp(
                utils.ValidationError, 'exploration has no editors'):
            exp_services.save_exploration(exploration)

    def test_init_state_property(self):
        """Test the init_state property."""
        INIT_STATE_ID = 'init_state_id'
        INIT_STATE_NAME = 'init_state_name'

        init_state = exp_domain.State(
            INIT_STATE_ID, INIT_STATE_NAME, [], [], None)
        exp_services.save_state(init_state)

        exploration = FakeExploration(owner_id='owner@example.com')
        exploration.states = [init_state]
        self.assertEqual(exploration.init_state_id, INIT_STATE_ID)
        self.assertEqual(exploration.init_state.name, INIT_STATE_NAME)

        second_state = exp_domain.State(
            'unused_second_state', 'unused', [], [], None)
        exploration.states.append(second_state)
        self.assertEqual(exploration.init_state_id, INIT_STATE_ID)
        self.assertEqual(exploration.init_state.name, INIT_STATE_NAME)

    def test_is_demo_property(self):
        """Test the is_demo property."""
        demo = FakeExploration(exp_id='0')
        self.assertEqual(demo.is_demo, True)

        notdemo1 = FakeExploration(exp_id='a')
        self.assertEqual(notdemo1.is_demo, False)

        notdemo2 = FakeExploration(exp_id='abcd')
        self.assertEqual(notdemo2.is_demo, False)

    def test_is_owned_by(self):
        """Test the is_owned_by() method."""
        owner_id = 'owner@example.com'
        editor_id = 'editor@example.com'
        viewer_id = 'viewer@example.com'

        exploration = FakeExploration(owner_id=owner_id)
        exploration.add_editor(editor_id)

        self.assertTrue(exploration.is_owned_by(owner_id))
        self.assertFalse(exploration.is_owned_by(editor_id))
        self.assertFalse(exploration.is_owned_by(viewer_id))
        self.assertFalse(exploration.is_owned_by(None))

    def test_is_editable_by(self):
        """Test the is_editable_by() method."""
        owner_id = 'owner@example.com'
        editor_id = 'editor@example.com'
        viewer_id = 'viewer@example.com'

        exploration = FakeExploration(owner_id=owner_id)
        exploration.add_editor(editor_id)

        self.assertTrue(exploration.is_editable_by(owner_id))
        self.assertTrue(exploration.is_editable_by(editor_id))
        self.assertFalse(exploration.is_editable_by(viewer_id))
        self.assertFalse(exploration.is_editable_by(None))