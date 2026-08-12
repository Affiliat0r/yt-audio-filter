/**
 * Colour vocabulary for the toy-train world.
 *
 * Bright, clean, child-friendly. Kept in one place so the whole scene stays
 * visually coherent and so a later "theme" knob has a single seam to turn.
 */

export const SKY = {
  zenith: 0x2e7fd6,
  mid: 0x7cc0f2,
  horizon: 0xdff0ff,
  sun: 0xfff6d0,
  sunHalo: 0xffe9a8,
  cloud: 0xffffff,
  cloudShade: 0xdbe8f6,
  fog: 0xcfe6f7,
};

export const LAND = {
  grassLight: 0x8fce54,
  grassMid: 0x6fb840,
  grassDeep: 0x4f9a34,
  grassDry: 0xc3d861,
  rock: 0x9aa2a8,
  sand: 0xdcc98a,
  water: 0x3d86d6,
};

export const TRACK = {
  ballast: 0xb9ab93,
  sleeper: 0x8b6647,
  rail: 0xb6bec6,
  postBody: 0xf6f2e8,
  postCap: 0xe0503f,
};

export const TRAIN = {
  bodyRed: 0xd8453c,
  bodyDark: 0x8f2b26,
  chassis: 0x3b3f46,
  funnel: 0x2c3036,
  brass: 0xe8b23c,
  cabYellow: 0xf5c542,
  roofRed: 0xc23a32,
  window: 0x9fd8ef,
  wheel: 0x33373d,
  wheelRim: 0xd6a13a,
  rod: 0xc8cdd3,
  lamp: 0xfff3c4,
};

export const CARRIAGE = [
  { body: 0x3f8fd8, roof: 0xe3eaf1, trim: 0xfad25a },
  { body: 0x58bf6b, roof: 0xe3eaf1, trim: 0xf07d4a },
  { body: 0xf0a63c, roof: 0xe3eaf1, trim: 0x4f9ad8 },
  { body: 0xb073d6, roof: 0xe3eaf1, trim: 0x7fd6c4 },
];

export const FOLIAGE = [0x4fa83f, 0x63bd4a, 0x3f8f38, 0x7ac455, 0x559c46];
export const BLOSSOM = [0xf49bc1, 0xf7c948, 0xf2f2f2];
export const TRUNK = [0x8a5f3c, 0x74502f, 0x9a6b45];
export const ROCK = [0x9aa2a8, 0x878f96, 0xadb4b9];

export const HOUSE = {
  walls: [0xf6ead4, 0xf7d7b0, 0xe8f0f7, 0xfbe1e1, 0xe6f2dc],
  roofs: [0xd9564a, 0x4a7fbf, 0x54a86a, 0xe0913c, 0x7d6bb0],
  door: 0x8a5f3c,
  window: 0x9fd8ef,
  chimney: 0xc06a52,
};

export const PEOPLE = {
  skin: [0xf2c9a0, 0xd9a26e, 0xa9713f, 0x8a5a30, 0xf7ddc0],
  shirt: [0xe2564f, 0x4f8fd8, 0x5cb867, 0xf0b23c, 0xb073d6, 0xf2f2f2, 0x4fc0c0],
  trousers: [0x3a4a63, 0x5a4632, 0x2f5f4a, 0x6b3b52, 0x37474f],
  hat: [0xe2564f, 0x4f8fd8, 0xf0b23c, 0x2f5f4a],
};
