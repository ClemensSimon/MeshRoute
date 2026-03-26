// WalkFlood Simulator - Scenario Definitions

// ---- Scenario Definitions ----
const SCENARIOS = {
  // Scale Tests
  small:     { nodes: 20,   area: 1000,  range: 800,  terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0 },
  medium:    { nodes: 80,   area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0 },
  large:     { nodes: 200,  area: 15000, range: 3000, terrain:'suburban',    placement:'random',    mobile:0,   kills:0,   degrade:0 },
  dense:     { nodes: 120,  area: 2000,  range: 1000, terrain:'dense_urban', placement:'random',    mobile:0,   kills:0,   degrade:0 },

  // Terrain & Topology
  trail:     { nodes: 30,   area: 8000,  range: 2000, terrain:'rural',       placement:'linear',    mobile:0,   kills:0,   degrade:0 },
  mountain:  { nodes: 60,   area: 12000, range: 2000, terrain:'dense_urban', placement:'random',    mobile:0,   kills:0,   degrade:0.2 },
  maritime:  { nodes: 30,   area: 25000, range: 8000, terrain:'rural',       placement:'random',    mobile:0,   kills:0,   degrade:0 },
  community: { nodes: 80,   area: 8000,  range: 2500, terrain:'suburban',    placement:'clustered', mobile:0,   kills:0,   degrade:0 },

  // Mobile & Events
  festival:  { nodes: 100,  area: 2000,  range: 1500, terrain:'suburban',    placement:'clustered', mobile:0.5, kills:0,   degrade:0 },
  building:  { nodes: 150,  area: 500,   range: 300,  terrain:'dense_urban', placement:'clustered', mobile:0.8, kills:0,   degrade:0 },
  highway:   { nodes: 50,   area: 10000, range: 3000, terrain:'rural',       placement:'linear',    mobile:0.9, kills:0,   degrade:0 },

  // Stress Tests
  disaster:  { nodes: 60,   area: 10000, range: 3000, terrain:'suburban',    placement:'random',    mobile:0.2, kills:0.2, degrade:0.3 },
  partition: { nodes: 120,  area: 8000,  range: 2500, terrain:'urban',       placement:'random',    mobile:0,   kills:0.4, degrade:0.4 },

  // Bay Area (Real-World) — 3-tier elevation: mountain/hill/valley
  bayarea:      { nodes: 235,  area: 50000, range: 5000, terrain:'urban', placement:'bay_area', mobile:0, kills:0,    degrade:0 },
  bayarea_s:    { nodes: 235,  area: 50000, range: 5000, terrain:'urban', placement:'bay_area', mobile:0, kills:0.15, degrade:0.2 },
  bayarea1200:  { nodes: 1200, area: 50000, range: 5000, terrain:'urban', placement:'bay_area', mobile:0, kills:0,    degrade:0 },

  // Broadcast Comparison
  bc_medium:    { nodes: 80,   area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0, kills:0,    degrade:0,   broadcastMode: true },
  bc_bayarea:   { nodes: 235,  area: 50000, range: 5000, terrain:'urban',       placement:'bay_area',  mobile:0, kills:0,    degrade:0,   broadcastMode: true },
};
