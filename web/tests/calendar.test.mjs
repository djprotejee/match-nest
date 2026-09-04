import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Render the actual calendar component without a browser or network access.
const src = fileURLToPath(new URL("../src/", import.meta.url));
globalThis.window = { location: { origin: "http://localhost" } };
globalThis.localStorage = { getItem: () => null };
const app = fs.readFileSync(path.join(src, "App.tsx"), "utf8");
const bundled = buildSync({
  stdin: { contents: app + '\nexport { CalendarScreen };\nexport * from "./calendarState";\nexport { filterGroups } from "./eventFilters";', resolveDir: src, loader: "tsx" },
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react/*"], define: { "import.meta.env": "{}" },
}).outputFiles[0].text;
const compiled = new Module(path.join(src, "calendar-test.cjs"));
compiled.paths = Module._nodeModulePaths(src);
compiled._compile(bundled, path.join(src, "calendar-test.cjs"));
const { CalendarScreen, CALENDAR_STATUSES, CALENDAR_DEFAULTS_VERSION, restoredCalendarStatuses, calendarLoadMessage, shiftedCalendarMonth, filterGroups } = compiled.exports;
const noop = () => {};
const base = {
  groups: [], loading: false, refreshing: false, error: null, totalEvents: 0,
  onShowAll: noop, onRetry: noop, monthCursor: new Date(2026, 7, 1), setMonthCursor: noop,
  feedMode: "main", setFeedMode: noop, categories: [], customFeedLevels: [], toggleCustomFeedLevel: noop,
  importanceMode: "all", setImportanceMode: noop, statuses: new Set(CALENDAR_STATUSES), toggleStatus: noop,
  sports: new Set(["football","formula","cs2"]), toggleSport: noop, hideSpoilers: true,
  setHideSpoilers: noop, watch: {}, revealed: {}, onWatch: noop, onReveal: noop, onRemoveManual: noop,
};
const fixture = (month, status) => ({date: `2026-${month}-15`, events: [{
  id: `match-${month}`, title: `Month ${month} fixture`, sport: "football", starts_at: `2026-${month}-15T15:00:00Z`,
  status, entity_ids: ["barcelona"], source: "football-data", follow_level: "main", importance: 80, result_hidden: true,
}]});
const render = (props) => renderToStaticMarkup(React.createElement(CalendarScreen, {...base,...props}));

test("the exact saved future-only v6 calendar settings restore past and TBD", () => {
  const statuses = restoredCalendarStatuses({statusDefaultsVersion:6, calendarStatuses:["live","delayed","upcoming"]});
  assert.deepEqual([...statuses],CALENDAR_STATUSES);
});

test("explicit calendar filters are preserved after its own migration", () => {
  assert.deepEqual([...restoredCalendarStatuses({calendarStatusDefaultsVersion:CALENDAR_DEFAULTS_VERSION,calendarStatuses:["past"]})],["past"]);
});

test("past and future month events survive restored filters and render in the calendar", () => {
  const statuses=restoredCalendarStatuses({statusDefaultsVersion:6,calendarStatuses:["live","delayed","upcoming"]});
  for (const [month,status] of [["07","past"],["08","past"],["10","upcoming"],["11","upcoming"]]) {
    const groups=filterGroups([fixture(month,status)],{sports:base.sports,statuses,feedMode:"main",customFeedLevels:[],importanceMode:"all",state:{watch:{},f1Sessions:{}},entityMap:new Map()});
    assert.equal(groups.length,1);
    const html=render({groups,totalEvents:1,monthCursor:new Date(2026,Number(month)-1,1)});
    assert.ok(html.includes(`Month ${month} fixture`));
    assert.ok(!html.includes("No followed events"));
  }
});

test("pending months show loading instead of an incorrect empty state", () => {
  const html=render({loading:true});
  assert.ok(html.includes("Loading this month"));
  assert.ok(!html.includes("No followed events"));
  assert.ok(render({refreshing:true}).includes("Fetching matches for this month"));
});

test("filtered-out events offer a visible recovery action", () => {
  const html=render({totalEvents:14});
  assert.ok(html.includes("14 events are hidden by your calendar filters"));
  assert.ok(html.includes("Show all statuses and sports"));
  assert.ok(!html.includes("No followed events"));
});

test("request errors have a retry action and cached matches stay visible", () => {
  const html=render({groups:[fixture("08","past")],totalEvents:1,error:"Connection failed"});
  assert.ok(html.includes("Connection failed"));
  assert.ok(html.includes("Retry"));
  assert.ok(html.includes("Month 08 fixture"));
});

test("month navigation works at month-end and year boundaries", () => {
  assert.equal(shiftedCalendarMonth(new Date(2026,0,31),1).getMonth(),1);
  const previous=shiftedCalendarMonth(new Date(2026,0,31),-1);
  assert.equal(previous.getFullYear(),2025);
  assert.equal(previous.getMonth(),11);
});
