// MeshRoute Simulator - Scenario Definitions

    this.lineTo(x, y + tl);
    this.quadraticCurveTo(x, y, x + tl, y);
    this.closePath();
  };
}

// ---- Scenario Definitions ----
const SCENARIOS = {
  small:     { nodes: 20,  area: 1000,  range: 800,  terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0 },
  medium:    { nodes: 80,  area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0 },
  large:     { nodes: 200, area: 15000, range: 3000, terrain:'suburban',    placement:'random',    mobile:0,   kills:0,   degrade:0 },
  trail:     { nodes: 30,  area: 8000,  range: 2000, terrain:'rural',       placement:'linear',    mobile:0,   kills:0,   degrade:0 },
  festival:  { nodes: 100, area: 2000,  range: 1500, terrain:'suburban',    placement:'clustered', mobile:0.5, kills:0,   degrade:0 },
  disaster:  { nodes: 60,  area: 10000, range: 3000, terrain:'suburban',    placement:'random',    mobile:0.2, kills:0.2, degrade:0.3 },
  dense:     { nodes: 120, area: 2000,  range: 1000, terrain:'dense_urban', placement:'random',    mobile:0,   kills:0,   degrade:0 },
  mountain:  { nodes: 60,  area: 12000, range: 2000, terrain:'dense_urban', placement:'random',    mobile:0,   kills:0,   degrade:0.2 },
  maritime:  { nodes: 30,  area: 25000, range: 8000, terrain:'rural',       placement:'random',    mobile:0,   kills:0,   degrade:0 },
  building:  { nodes: 150, area: 500,   range: 300,  terrain:'dense_urban', placement:'clustered', mobile:0.8, kills:0,   degrade:0 },
  highway:   { nodes: 50,  area: 10000, range: 3000, terrain:'rural',       placement:'linear',    mobile:0.9, kills:0,   degrade:0 },
  community: { nodes: 80,  area: 8000,  range: 2500, terrain:'suburban',    placement:'clustered', mobile:0,   kills:0,   degrade:0 },
  partition: { nodes: 120, area: 8000,  range: 2500, terrain:'urban',       placement:'random',    mobile:0,   kills:0.4, degrade:0.4 },
  bayarea:   { nodes: 235, area: 50000, range: 5000, terrain:'urban',       placement:'bay_area',  mobile:0,   kills:0,    degrade:0,   silencing:0 },
  bayarea_s: { nodes: 235, area: 50000, range: 5000, terrain:'urban',       placement:'bay_area',  mobile:0,   kills:0.15, degrade:0.2, silencing:0 },
  bayarea_silent:   { nodes: 235, area: 50000, range: 5000, terrain:'urban', placement:'bay_area', mobile:0,   kills:0,    degrade:0,   silencing:0.6 },
  bayarea_silent_s: { nodes: 235, area: 50000, range: 5000, terrain:'urban', placement:'bay_area', mobile:0,   kills:0.15, degrade:0.2, silencing:0.6 },
  // Mixed-mode: S5 nodes coexist with legacy flooding nodes
  // Broadcast comparison: Managed Flood vs Cluster-Distributor
  bc_medium: { nodes: 50,  area: 3000,  range: 1200, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0, broadcastMode:true },
  bc_large:  { nodes: 100, area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0, broadcastMode:true },
  bc_dense:  { nodes: 200, area: 3000,  range: 1200, terrain:'dense_urban', placement:'random',    mobile:0,   kills:0,   degrade:0, broadcastMode:true },
  bc_bayarea:{ nodes: 235, area: 50000, range: 5000, terrain:'urban',       placement:'bay_area',  mobile:0,   kills:0,   degrade:0, broadcastMode:true },
  mixed10:   { nodes: 80,  area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0, s5ratio:0.1 },
  mixed30:   { nodes: 80,  area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0, s5ratio:0.3 },
  mixed50:   { nodes: 80,  area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0, s5ratio:0.5 },
  mixed70:   { nodes: 80,  area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0, s5ratio:0.7 },
  mixed90:   { nodes: 80,  area: 5000,  range: 2000, terrain:'urban',       placement:'random',    mobile:0,   kills:0,   degrade:0, s5ratio:0.9 },
};

