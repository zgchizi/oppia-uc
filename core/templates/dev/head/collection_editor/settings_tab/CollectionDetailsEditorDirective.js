// Copyright 2016 The Oppia Authors. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS-IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * @fileoverview Directive for displaying and editing a collection details.
 * Edit options include: changing the title, objective, and category, and also
 * adding a new exploration.
 */

oppia.directive('collectionDetailsEditor', [function() {
  return {
    restrict: 'E',
    templateUrl: 'inline/collection_details_editor_directive',
    controller: [
      '$scope', 'CollectionEditorStateService', 'CollectionUpdateService',
      'alertsService', 'CATEGORY_LIST', 'EVENT_COLLECTION_INITIALIZED',
      'EVENT_COLLECTION_REINITIALIZED', 'COLLECTION_TITLE_INPUT_FOCUS_LABEL',
      function(
          $scope, CollectionEditorStateService, CollectionUpdateService,
          alertsService, CATEGORY_LIST, EVENT_COLLECTION_INITIALIZED,
          EVENT_COLLECTION_REINITIALIZED, COLLECTION_TITLE_INPUT_FOCUS_LABEL) {
        $scope.collection = CollectionEditorStateService.getCollection();
        $scope.COLLECTION_TITLE_INPUT_FOCUS_LABEL = (
          COLLECTION_TITLE_INPUT_FOCUS_LABEL);
        $scope.hasPageLoaded = (
          CollectionEditorStateService.hasLoadedCollection);
        $scope.CATEGORY_LIST_FOR_SELECT2 = CATEGORY_LIST.map(
          function(category) {
            return {
              id: category,
              text: category
            };
          }
        );

        var refreshSettingsTab = function() {
          $scope.displayedCollectionTitle = $scope.collection.getTitle();
          $scope.displayedCollectionObjective = (
            $scope.collection.getObjective());
          $scope.displayedCollectionCategory = (
            $scope.collection.getCategory());

          var categoryIsInSelect2 = $scope.CATEGORY_LIST_FOR_SELECT2.some(
            function(categoryItem) {
              return categoryItem.id === $scope.collection.getCategory();
            }
          );

          // If the current category is not in the dropdown, add it as the first
          // option.
          if (!categoryIsInSelect2 && $scope.collection.getCategory()) {
            $scope.CATEGORY_LIST_FOR_SELECT2.unshift({
              id: $scope.collection.getCategory(),
              text: $scope.collection.getCategory()
            });
          }
        };

        $scope.$on(EVENT_COLLECTION_INITIALIZED, refreshSettingsTab);
        $scope.$on(EVENT_COLLECTION_REINITIALIZED, refreshSettingsTab);

        $scope.updateCollectionTitle = function() {
          if (!$scope.displayedCollectionTitle) {
            alertsService.addWarning(
              '请指定名称.');
            return;
          }
          CollectionUpdateService.setCollectionTitle(
            $scope.collection, $scope.displayedCollectionTitle);
        };

        $scope.updateCollectionObjective = function() {
          if (!$scope.displayedCollectionObjective) {
            alertsService.addWarning(
              '请指定目标.');
            return;
          }
          CollectionUpdateService.setCollectionObjective(
            $scope.collection, $scope.displayedCollectionObjective);
        };

        $scope.updateCollectionCategory = function() {
          if (!$scope.displayedCollectionCategory) {
            alertsService.addWarning(
              '请指定分类.');
            return;
          }
          CollectionUpdateService.setCollectionCategory(
            $scope.collection, $scope.displayedCollectionCategory);
        };
      }
    ]
  };
}]);
