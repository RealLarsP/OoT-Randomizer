from __future__ import annotations
import sys
from collections import defaultdict
from collections.abc import Iterable, Collection
from typing import TYPE_CHECKING, Optional, Any

from HintList import BOSS_GOAL_TABLE, REWARD_GOAL_TABLE, get_hint_group, hint_exclusions
from ItemList import item_table
from RulesCommon import AccessRule
from Search import Search, ValidGoals

if sys.version_info >= (3, 10):
    from typing import TypeAlias
else:
    TypeAlias = str

if TYPE_CHECKING:
    from Location import Location
    from Spoiler import Spoiler
    from State import State
    from World import World

RequiredLocations: TypeAlias = "dict[str, dict[str, dict[int, list[tuple[Location, int, int]]]] | list[Location]]"
GoalItem: TypeAlias = "dict[str, str | int | bool]"

validColors: list[str] = [
    'White',
    'Red',
    'Green',
    'Blue',
    'Light Blue',
    'Pink',
    'Yellow',
    'Black',
]


class Goal:
    def __init__(self, world: World, name: str, hint_text: str | dict[str, str], color: str, items: Optional[list[dict[str, Any]]] = None,
                 locations=None, lock_locations=None, lock_entrances: Optional[list[str]] = None, required_locations=None, create_empty: bool = False) -> None:
        # early exit if goal initialized incorrectly
        if not items and not locations and not create_empty:
            raise Exception('Invalid goal: no items or destinations set')
        if color not in validColors:
            raise Exception(f'Invalid goal: Color {color} not supported')

        self.world: World = world
        self.name: str = name
        self.hint_text: str | dict[str, str] = hint_text
        self.color: str = color
        self.items: list[GoalItem] = items or []
        self.locations = locations  # Unused?
        self.lock_locations = lock_locations  # Unused?
        self.lock_entrances: list[str] = lock_entrances or []
        self.required_locations: list[tuple[Location, int, int, list[int]]] = required_locations or []
        self.weight: int = 0
        self.category: Optional[GoalCategory] = None
        self._item_cache: dict[str, GoalItem] = {}

    def copy(self) -> Goal:
        new_goal = Goal(self.world, self.name, self.hint_text, self.color, self.items, self.locations, self.lock_locations,
                        self.lock_entrances, self.required_locations, True)
        return new_goal

    def get_item(self, item: str) -> GoalItem:
        try:
            return self._item_cache[item]
        except KeyError:
            for i in self.items:
                if i['name'] == item:
                    self._item_cache[item] = i
                    return i
        raise KeyError('No such item %r for goal %r' % (item, self.name))

    def requires(self, item: str) -> bool:
        # Prevent direct hints for certain items that can have many duplicates, such as tokens and Triforce Pieces
        names = [item]
        if item_table[item][3] is not None and 'alias' in item_table[item][3]:
            names.append(item_table[item][3]['alias'][0])
        return any(i['name'] in names and not i['hintable'] for i in self.items)

    def __repr__(self) -> str:
        return f"{self.world.__repr__()} {self.name}: {self.hint_text}"


class GoalCategory:
    def __init__(self, name: str, priority: int, goal_count: int = 0, minimum_goals: int = 0,
                 lock_locations=None, lock_entrances: list[str] = None) -> None:
        self.name: str = name
        self.priority: int = priority
        self.lock_locations = lock_locations  # Unused?
        self.lock_entrances: list[str] = lock_entrances
        self.goals: list[Goal] = []
        self.goal_count: int = goal_count
        self.minimum_goals: int = minimum_goals
        self.weight: int = 0
        self._goal_cache: dict[str, Goal] = {}

    def copy(self) -> GoalCategory:
        new_category = GoalCategory(self.name, self.priority, self.goal_count, self.minimum_goals, self.lock_locations, self.lock_entrances)
        new_category.goals = list(goal.copy() for goal in self.goals)
        return new_category

    def add_goal(self, goal) -> None:
        goal.category = self
        self.goals.append(goal)

    def get_goal(self, goal) -> Goal:
        if isinstance(goal, Goal):
            return goal
        try:
            return self._goal_cache[goal]
        except KeyError:
            for g in self.goals:
                if g.name == goal:
                    self._goal_cache[goal] = g
                    return g
        raise KeyError('No such goal %r' % goal)

    def is_beaten(self, search: Search) -> bool:
        # if the category requirements are already satisfied by starting items (including skipped locations),
        # do not generate hints for other goals in the category
        starting_goals = search.beatable_goals_fast({ self.name: self })
        return all(map(lambda s: len(starting_goals[self.name]['stateReverse'][s.world.id]) >= self.minimum_goals, search.state_list))

    def update_reachable_goals(self, starting_search: Search, full_search: Search) -> None:
        # Only reduce goal item quantity if minimum goal requirements are reachable,
        # but not the full goal quantity. Primary use is to identify reachable
        # skull tokens, triforce pieces, and plentiful item duplicates with
        # All Locations Reachable off. Beatable check is required to prevent
        # inadvertently setting goal requirements to 0 or some quantity less
        # than the minimum
        # Relies on consistent list order to reference between current state
        # and the copied search
        # IMPORTANT: This is not meant to be called per-world. It is supposed to
        # be called once for all world states for each category type.

        for index, state in enumerate(starting_search.state_list):
            world_category = state.world.goal_categories.get(self.name, None)
            if world_category is None:
                continue
            for goal in world_category.goals:
                if goal.items:
                    if all(map(full_search.state_list[index].has_item_goal, goal.items)):
                        for i in goal.items:
                            i['quantity'] = min(full_search.state_list[index].item_name_count(i['name']), i['quantity'])


def replace_goal_names(worlds: list[World]) -> None:
    for world in worlds:
        if world.settings.shuffle_dungeon_rewards in ('vanilla', 'reward'):
            bosses = [
                location
                for location in world.get_filled_locations()
                if location.type == 'Boss'
                or (location.name == 'ToT Reward from Rauru' and not world.settings.skip_reward_from_rauru)
            ]
            for category in world.goal_categories.values():
                for goal in category.goals:
                    if isinstance(goal.hint_text, dict):
                        for boss in bosses:
                            if boss.item.name == goal.hint_text['replace']:
                                flavor_text, clear_text, color = BOSS_GOAL_TABLE[boss.name]
                                if world.settings.clearer_hints:
                                    goal.hint_text = clear_text
                                else:
                                    goal.hint_text = flavor_text
                                goal.color = color
                                break
        else:
            for category in world.goal_categories.values():
                for goal in category.goals:
                    if isinstance(goal.hint_text, dict):
                        flavor_text, clear_text = REWARD_GOAL_TABLE[goal.hint_text['replace']]
                        if world.settings.clearer_hints:
                            goal.hint_text = clear_text
                        else:
                            goal.hint_text = flavor_text


def update_goal_items(spoiler: Spoiler) -> None:
    worlds = spoiler.worlds

    # get list of all the progressive items that can appear in hints
    # all_locations: all progressive items. have to collect from these
    # item_locations: only the ones that should appear as "required"/WotH
    all_locations = [location for world in worlds for location in world.get_filled_locations()]
    # Set to test inclusion against
    item_locations = {location for location in all_locations if location.item.majoritem and not location.locked}

    # required_locations[category.name][goal.name][world_id] = [...]
    required_locations: RequiredLocations = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    priority_locations = {world.id: {} for world in worlds}

    # rebuild hint exclusion list
    for world in worlds:
        hint_exclusions(world, clear_cache=True)

    # getHintGroup relies on hint exclusion list
    always_locations = [location.name for world in worlds for location in get_hint_group('always', world)]

    if worlds[0].enable_goal_hints:
        # References first world for goal categories only
        for cat_name, category in worlds[0].locked_goal_categories.items():
            for cat_world in worlds:
                search = Search([world.state for world in worlds])
                search.collect_pseudo_starting_items()

                cat_state = list(filter(lambda s: s.world.id == cat_world.id, search.state_list))
                category_locks = lock_category_entrances(category, cat_state)

                if category.is_beaten(search):
                    unlock_category_entrances(category_locks, cat_state)
                    continue

                full_search = search.copy()
                full_search.collect_locations()
                reachable_goals = {}
                # Goals are changed for beatable-only accessibility per-world
                category.update_reachable_goals(search, full_search)
                reachable_goals = full_search.beatable_goals_fast({ cat_name: category }, cat_world.id)
                identified_locations = search_goals({ cat_name: category }, reachable_goals, search, priority_locations, all_locations, item_locations, always_locations)
                # Multiworld can have all goals for one player's bridge entirely
                # locked by another player's bridge. Therefore, we can't assume
                # accurate required location lists by locking every world's
                # entrance locks simultaneously. We must run a new search on the
                # same category for each world's entrance locks.
                for goal_name, world_location_lists in identified_locations[cat_name].items():
                    required_locations[cat_name][goal_name][cat_world.id] = list(identified_locations[cat_name][goal_name][cat_world.id])

                unlock_category_entrances(category_locks, cat_state)

    search = Search([world.state for world in worlds])
    search.collect_pseudo_starting_items()
    reachable_goals = {}
    # Force all goals to be unreachable if goal hints are not part of the distro
    # Saves a minor amount of search time. Most of the benefit is skipping the
    # locked goal categories. This section still needs to run to generate WOTH.
    # WOTH isn't a goal, so it still is searched successfully.
    if worlds[0].enable_goal_hints:
        full_search = search.copy()
        full_search.collect_locations()
        for cat_name, category in worlds[0].unlocked_goal_categories.items():
            category.update_reachable_goals(search, full_search)
        reachable_goals = full_search.beatable_goals_fast(worlds[0].unlocked_goal_categories)
    identified_locations = search_goals(worlds[0].unlocked_goal_categories, reachable_goals, search, priority_locations, all_locations, item_locations, always_locations, search_woth=True)
    required_locations.update(identified_locations)
    woth_locations = list(required_locations['way of the hero'])
    del required_locations['way of the hero']

    # Update WOTH items
    woth_locations_dict = {}
    for world in worlds:
        woth_locations_dict[world.id] = list(filter(lambda location: location.world.id == world.id, woth_locations))
    spoiler.required_locations = woth_locations_dict

    # Fallback to way of the hero required items list if all goals/goal categories already satisfied.
    # Do not use if the default woth-like goal was already added for open bridge/open ganon.
    # If the woth list is also empty, fails gracefully to the next hint type for the distro in either case.
    # required_locations_dict[goal_world][category.name][goal.name][world.id] = [...]
    required_locations_dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    if not required_locations and 'ganon' not in worlds[0].goal_categories and worlds[0].hint_dist_user['use_default_goals'] and worlds[0].enable_goal_hints:
        for world in worlds:
            locations = [(location, 1, 1, [world.id]) for location in spoiler.required_locations[world.id]]
            c = GoalCategory('ganon', 30, goal_count=1, minimum_goals=1)
            g = Goal(world, 'the hero', 'way of the hero', 'White', items=[{'name': 'Triforce', 'quantity': 1, 'minimum': 1, 'hintable': True}])
            g.required_locations = locations
            c.add_goal(g)
            world.goal_categories[c.name] = c
            # The real protagonist of the story
            required_locations_dict[world.id]['ganon']['the hero'][world.id] = [l[0] for l in locations]
            spoiler.goal_categories[world.id] = {c.name: c.copy()}
    else:
        for world in worlds:
            for cat_name, category in world.goal_categories.items():
                if cat_name not in required_locations:
                    continue
                for goal in category.goals:
                    if goal.name not in required_locations[category.name]:
                        continue
                    # Filter the required locations to only include locations in the goal's world.
                    # The fulfilled goal can be for another world, whose ID is saved previously as
                    # the fourth entry in each location tuple
                    goal_locations = defaultdict(list)
                    for goal_world, locations in required_locations[category.name][goal.name].items():
                        for location in locations:
                            # filter isn't strictly necessary for the spoiler dictionary, but this way
                            # we only loop once to do that and grab the required locations for the
                            # current goal
                            if location[0].world.id != world.id:
                                continue
                            # The spoiler shows locations across worlds contributing to this world's goal
                            required_locations_dict[goal_world][category.name][goal.name][world.id].append(location[0])
                            goal_locations[location[0]].append(goal_world)
                    goal.required_locations = [(location, 1, 1, world_ids) for location, world_ids in goal_locations.items()]
        # Copy of goal categories for the spoiler log to reference
        # since the hint algorithm mutates the world copy
        for world in worlds:
            spoiler.goal_categories[world.id] = {cat_name: category.copy() for cat_name, category in world.goal_categories.items()}
    spoiler.goal_locations = required_locations_dict


def lock_category_entrances(category: GoalCategory, state_list: Iterable[State]) -> dict[int, dict[str, AccessRule]]:
    # Disable access rules for specified entrances
    category_locks = {}
    if category.lock_entrances is not None:
        for index, state in enumerate(state_list):
            category_locks[index] = {}
            for lock in category.lock_entrances:
                exit = state.world.get_entrance(lock)
                category_locks[index][exit.name] = exit.access_rule
                exit.access_rule = lambda state, **kwargs: False
    return category_locks


def unlock_category_entrances(category_locks: dict[int, dict[str, AccessRule]],
                              state_list: list[State]) -> None:
    # Restore access rules
    for state_id, exits in category_locks.items():
        for exit_name, access_rule in exits.items():
            exit = state_list[state_id].world.get_entrance(exit_name)
            exit.access_rule = access_rule


def search_goals(categories: dict[str, GoalCategory], reachable_goals: ValidGoals, search: Search, priority_locations: dict[int, dict[str, str]],
                 all_locations: list[Location], item_locations: Collection[Location], always_locations: Collection[str],
                 search_woth: bool = False) -> RequiredLocations:
    # required_locations[category.name][goal.name][world_id] = [...]
    required_locations: RequiredLocations = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    world_ids = [state.world.id for state in search.state_list]
    if search_woth:
        required_locations['way of the hero'] = []
    remaining_locations = all_locations[:]
    for location in search.iter_reachable_locations(all_locations):
        # Try to remove items one at a time and see if the goal is still reachable
        if location in item_locations:
            old_item = location.item
            location.item = None
            # copies state! This is very important as we're in the middle of a search
            # already, but beneficially, has search it can start from
            valid_goals = search.beatable_goals(categories)
            for cat_name, category in categories.items():
                # Exit early if no goals are beatable with category locks
                if category.name in reachable_goals and reachable_goals[category.name]:
                    for goal in category.goals:
                        if (category.name in valid_goals
                                and goal.name in valid_goals[category.name]
                                and goal.name in reachable_goals[category.name]
                                and (location.name not in priority_locations[location.world.id]
                                     or priority_locations[location.world.id][location.name] == category.name)
                                and not goal.requires(old_item.name)):
                            invalid_states = set(world_ids) - set(valid_goals[category.name][goal.name])
                            hintable_states = list(invalid_states & set(reachable_goals[category.name][goal.name]))
                            if hintable_states:
                                for world_id in hintable_states:
                                    # Placeholder weights to be set for future bottleneck targeting.
                                    # 0 weights for always-hinted locations isn't relevant currently
                                    # but is intended to screen out contributions to the overall
                                    # goal/category weights
                                    if location.name in always_locations or location.name in location.world.hint_exclusions:
                                        location_weights = (location, 0, 0)
                                    else:
                                        location_weights = (location, 1, 1)
                                    required_locations[category.name][goal.name][world_id].append(location_weights)
                                goal.weight = 1
                                category.weight = 1
                                # Locations added to goal exclusion for future categories
                                # Main use is to split goals between before/after rainbow bridge
                                # Requires goal categories to be sorted by priority!
                                priority_locations[location.world.id][location.name] = category.name
            if search_woth and not valid_goals['way of the hero']:
                required_locations['way of the hero'].append(location)
            location.item = old_item
        location.maybe_set_misc_hints()
        remaining_locations.remove(location)
        if location.item.solver_id is not None:
            search.state_list[location.item.world.id].collect(location.item)
    for location in remaining_locations:
        # finally, collect unreachable locations for misc. item hints
        location.maybe_set_misc_hints()
    return required_locations
