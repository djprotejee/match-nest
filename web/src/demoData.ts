import type { DayGroup, EntityItem } from "./types";

const today = new Date();
const isoDay = (offset: number) => {
  const date = new Date(today);
  date.setDate(today.getDate() + offset);
  return date.toISOString().slice(0, 10);
};

export const fallbackTimeline: DayGroup[] = [
  {
    date: isoDay(0),
    events: [
      {
        id: "fallback-navi",
        title: "NAVI vs Vitality",
        sport: "cs2",
        starts_at: new Date(today.getTime() + 1000 * 60 * 35).toISOString(),
        status: "upcoming",
        entity_ids: ["navi_cs2", "blast"],
        source: "local-fallback",
        competition: "BLAST",
        session_type: null,
        result_summary: null,
        importance: 92,
        follow_level: "main",
        result_hidden: false,
      },
    ],
  },
  {
    date: isoDay(1),
    events: [
      {
        id: "fallback-f1",
        title: "Formula 1 Grand Prix - Race",
        sport: "formula",
        starts_at: new Date(today.getTime() + 1000 * 60 * 60 * 24).toISOString(),
        status: "upcoming",
        entity_ids: ["f1"],
        source: "local-fallback",
        competition: "Formula 1",
        session_type: "race",
        result_summary: null,
        importance: 95,
        follow_level: "main",
        result_hidden: false,
      },
    ],
  },
  {
    date: isoDay(-1),
    events: [
      {
        id: "fallback-barca",
        title: "Barcelona vs Real Madrid",
        sport: "football",
        starts_at: new Date(today.getTime() - 1000 * 60 * 60 * 24).toISOString(),
        status: "past",
        entity_ids: ["barcelona"],
        source: "local-fallback",
        competition: "La Liga",
        session_type: null,
        result_summary: null,
        importance: 90,
        follow_level: "main",
        result_hidden: true,
      },
    ],
  },
];

export const fallbackEntities: EntityItem[] = [
  { id: "f1", name: "Formula 1", sport: "formula", kind: "competition", color: "#F04438", follow: "main" },
  { id: "navi_cs2", name: "NAVI CS2", sport: "cs2", kind: "team", color: "#F4B740", follow: "main" },
  { id: "ukraine_nt", name: "Ukraine NT", sport: "football", kind: "team", color: "#2ECC71", follow: "main" },
  { id: "barcelona", name: "Barcelona", sport: "football", kind: "team", color: "#2ECC71", follow: "main" },
  { id: "ferrari", name: "Ferrari", sport: "formula", kind: "team", color: "#DC2626", follow: "starred" },
  { id: "ucl", name: "UEFA Champions League", sport: "football", kind: "competition", color: "#8B5CF6", follow: "starred" },
  { id: "world_cup", name: "FIFA World Cup", sport: "football", kind: "competition", color: "#8B5CF6", follow: "starred" },
  { id: "euro", name: "UEFA Euro", sport: "football", kind: "competition", color: "#8B5CF6", follow: "starred" },
];

