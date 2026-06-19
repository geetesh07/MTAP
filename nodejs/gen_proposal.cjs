'use strict';
/**
 * gen_proposal.cjs — OCC-based drill proposal DXF generator.
 *
 * Identical pipeline to the 3D project (occDrill.ts → occProjection.ts → occDxf.ts).
 * Uses opencascade.js + three-edge-projection for true HLR engineering views.
 *
 * Usage:
 *   node gen_proposal.cjs <params_json> <output_dxf_path>
 *
 * params_json fields:
 *   cutting_diameter, shank_diameter, overall_length, shank_length,
 *   point_angle, helix_angle, n_flutes
 */

const path = require('path');
const fs   = require('fs');
const { pathToFileURL } = require('url');

// Self-contained: deps are bundled in nodejs/node_modules next to this script.
// Works identically from source and from the frozen exe (PyInstaller extracts
// the whole nodejs/ tree to _MEIPASS/nodejs).
const NM = path.join(__dirname, 'node_modules');

// ─── OCC bootstrap (three-fix pattern for Node.js v24) ───────────────────────

const occDir  = path.join(NM, 'opencascade.js', 'dist');
const occFile = path.join(occDir, 'opencascade.js');

let occSrc = fs.readFileSync(occFile, 'utf8');
occSrc = occSrc.replace(/^export default Module;?\s*$/m, '// (ESM export stripped for CJS compat)');

const ocModule = { exports: {} };
const wrapper  = new Function(
  'require', 'module', 'exports', '__filename', '__dirname',
  occSrc + '\nmodule.exports = { default: Module };'
);
wrapper(require, ocModule, ocModule.exports, occFile, occDir);
const ocFactory = ocModule.exports.default;

const _nativeFetch = global.fetch;
global.fetch = async (url, opts) => {
  const s = String(url);
  if (s.startsWith('file:')) {
    let p = decodeURIComponent(s.replace(/^file:\/+/, ''));
    if (!/^[a-zA-Z]:/.test(p)) p = '/' + p;
    const buf = fs.readFileSync(p);
    const ab  = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    return { ok: true, arrayBuffer: () => Promise.resolve(ab), headers: { get: () => null } };
  }
  if (_nativeFetch) return _nativeFetch(url, opts);
  throw new Error('no fetch for: ' + s);
};

const DYLIB = { loadAsync: true, global: true, nodelete: true, allowUndefined: false };
const toUrl  = f => pathToFileURL(f).href;

// ─── Dependencies from 3d/node_modules ───────────────────────────────────────

const THREE        = require(path.join(NM, 'three'));
const { MeshBVH }  = require(path.join(NM, 'three-mesh-bvh'));
const { ProjectionGenerator } = require(
  path.join(NM, 'three-edge-projection', 'src', 'ProjectionGenerator.js')
);
const Drawing = require(path.join(NM, 'dxf-writer'));

// ─── Args ─────────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
if (args.length < 2) {
  process.stderr.write('Usage: node gen_proposal.cjs <params.json|inline_json> <output_dxf_path>\n');
  process.exit(1);
}
// Accept either a path to a JSON file or an inline JSON string
const raw0    = args[0].trim();
const jsonStr = (raw0.startsWith('{') ? raw0 : fs.readFileSync(raw0, 'utf8'))
  .replace(/^﻿/, '').trim(); // strip UTF-8 BOM if present
const p       = JSON.parse(jsonStr);
const outPath = args[1];

const EPS = 1e-6;

// ─── Build helical flute cutter tube (exact port of occDrill.ts) ─────────────

function makeFluteCutter(oc, rBody, zStart, zEnd, phi, rho) {
  const helixRad = (p.helix_angle * Math.PI) / 180;
  const lead     = helixRad > EPS ? (2 * Math.PI * rBody) / Math.tan(helixRad) : Infinity;
  const B        = zEnd - zStart;
  const A        = isFinite(lead) ? (2 * Math.PI * B) / lead : 0;
  const M        = Math.sqrt(A * A + B * B);

  const cylAxis  = new oc.gp_Ax3_4(new oc.gp_Pnt_3(0, 0, 0), new oc.gp_Dir_4(0, 0, 1));
  const cyl      = new oc.Geom_CylindricalSurface_1(cylAxis, rBody);
  const line2d   = new oc.Geom2d_Line_3(
    new oc.gp_Pnt2d_3(phi, zStart),
    new oc.gp_Dir2d_4(A, B)
  );
  const helixEdge = new oc.BRepBuilderAPI_MakeEdge_31(
    new oc.Handle_Geom2d_Curve_2(line2d),
    new oc.Handle_Geom_Surface_2(cyl),
    0, M
  ).Edge();
  oc.BRepLib.BuildCurves3d_2(helixEdge);
  const spine = new oc.BRepBuilderAPI_MakeWire_2(helixEdge).Wire();

  const p0      = new oc.gp_Pnt_3(rBody * Math.cos(phi), rBody * Math.sin(phi), zStart);
  const tangent = new oc.gp_Dir_4(
    -rBody * Math.sin(phi) * A,
     rBody * Math.cos(phi) * A,
     B
  );
  const circ     = new oc.gp_Circ_2(new oc.gp_Ax2_3(p0, tangent), rho);
  const profEdge = new oc.BRepBuilderAPI_MakeEdge_8(circ).Edge();
  const profWire = new oc.BRepBuilderAPI_MakeWire_2(profEdge).Wire();
  const profFace = new oc.BRepBuilderAPI_MakeFace_15(profWire, true).Face();

  return new oc.BRepOffsetAPI_MakePipe_1(spine, profFace).Shape();
}

// ─── Build full drill solid ───────────────────────────────────────────────────

function buildSolid(oc) {
  const rBody  = Math.max(p.cutting_diameter,                              0.1) / 2;
  const rShank = Math.max(p.shank_diameter || p.cutting_diameter,          0.1) / 2;
  const paRad  = (p.point_angle * Math.PI) / 180;
  const tipH   = p.point_angle >= 180 ? 0 : rBody / Math.tan(paRad / 2);
  const chamH  = Math.abs(rBody - rShank);
  const bodyL  = Math.max(0.1, p.overall_length - p.shank_length - chamH - tipH);

  const zDir   = new oc.gp_Dir_4(0, 0, 1);
  const axisAt = z => new oc.gp_Ax2_3(new oc.gp_Pnt_3(0, 0, z), zDir);

  const parts = [];
  let z = 0;

  // Shank
  parts.push(new oc.BRepPrimAPI_MakeCylinder_3(axisAt(z), rShank, p.shank_length).Shape());
  z += p.shank_length;

  // Chamfer/transition
  if (chamH > EPS) {
    parts.push(new oc.BRepPrimAPI_MakeCone_3(axisAt(z), rShank, rBody, chamH).Shape());
    z += chamH;
  }

  // Fluted body
  const zBodyStart = z;
  parts.push(new oc.BRepPrimAPI_MakeCylinder_3(axisAt(z), rBody, bodyL).Shape());
  z += bodyL;

  // Tip cone
  if (tipH > EPS) {
    parts.push(new oc.BRepPrimAPI_MakeCone_3(axisAt(z), rBody, 0, tipH).Shape());
    z += tipH;
  }

  // Fuse all into one solid
  let shape = parts[0];
  for (let i = 1; i < parts.length; i++) {
    shape = new oc.BRepAlgoAPI_Fuse_3(shape, parts[i]).Shape();
  }

  // Cut helical flutes
  const nFlutes = p.n_flutes || 2;
  if (nFlutes >= 2) {
    try {
      const rho    = 0.3 * p.cutting_diameter;
      const zStart = zBodyStart - 0.5;
      const zEnd   = z;
      if (zEnd - zStart > 1) {
        for (let i = 0; i < nFlutes; i++) {
          const phi    = (2 * Math.PI * i) / nFlutes;
          const cutter = makeFluteCutter(oc, rBody, zStart, zEnd, phi, rho);
          shape = new oc.BRepAlgoAPI_Cut_3(shape, cutter).Shape();
        }
      }
    } catch (e) {
      process.stderr.write('[warn] flute cut failed, returning blank: ' + e.message + '\n');
    }
  }

  return shape;
}

// ─── Tessellate OCC shape → Three.js BufferGeometry ──────────────────────────

function shapeToGeometry(oc, shape) {
  new oc.BRepMesh_IncrementalMesh_2(shape, 0.04, false, 0.1, false);

  const positions = [];
  const indices   = [];
  let offset = 0;
  const rev = oc.TopAbs_Orientation.TopAbs_REVERSED;
  const exp = new oc.TopExp_Explorer_2(
    shape,
    oc.TopAbs_ShapeEnum.TopAbs_FACE,
    oc.TopAbs_ShapeEnum.TopAbs_SHAPE
  );

  for (; exp.More(); exp.Next()) {
    const face = oc.TopoDS.Face_1(exp.Current());
    const loc  = new oc.TopLoc_Location_1();
    const triH = oc.BRep_Tool.Triangulation(face, loc);
    if (triH.IsNull && triH.IsNull()) continue;
    const tri     = triH.get ? triH.get() : triH;
    const trsf    = loc.Transformation();
    const orient  = face.Orientation_1();
    const flipped = orient === rev || (orient && orient.value === rev.value);

    const nb = tri.NbNodes();
    for (let i = 1; i <= nb; i++) {
      const pt = tri.Node(i).Transformed(trsf);
      positions.push(pt.X(), pt.Y(), pt.Z());
    }
    const nt = tri.NbTriangles();
    for (let i = 1; i <= nt; i++) {
      const t = tri.Triangle(i);
      let a = t.Value(1), b = t.Value(2), c = t.Value(3);
      if (flipped) { const tmp = a; a = c; c = tmp; }
      indices.push(offset + a - 1, offset + b - 1, offset + c - 1);
    }
    offset += nb;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  geo.computeBoundingBox();
  geo.computeBoundingSphere();
  return geo;
}

// ─── HLR side-view projection ─────────────────────────────────────────────────

function projectSideView(geo) {
  const g = geo.clone();
  // Drill axis = Z in OCC; rotate so it becomes X in the projection plane
  g.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(0, Math.PI / 2, 0)));
  g.boundsTree = new MeshBVH(g);

  const mesh = new THREE.Mesh(g);
  const gen  = new ProjectionGenerator();
  gen.angleThreshold           = 30;
  gen.includeIntersectionEdges = true;

  // Use synchronous generator (no requestAnimationFrame needed in Node.js).
  // The result is the generator's *return* value (done===true), not a yielded value.
  const task = gen.generate(mesh);
  let step = task.next();
  while (!step.done) { step = task.next(); }
  return step.value.visibleEdges.getLineGeometry();
}

// ─── Extract 2D line segments from projected geometry ────────────────────────

function toSegments(geo) {
  const pos  = geo.getAttribute('position');
  const segs = [];
  if (!pos) return segs;
  for (let i = 0; i + 1 < pos.count; i += 2) {
    const x1 = pos.getX(i),   y1 = pos.getZ(i);
    const x2 = pos.getX(i+1), y2 = pos.getZ(i+1);
    if ([x1, y1, x2, y2].every(v => isFinite(v) && !isNaN(v))) {
      segs.push([x1, y1, x2, y2]);
    }
  }
  return segs;
}

// ─── B-rep edge extraction (port of occDrill.ts shapeToEdges) ────────────────
// Pulls the model's real edges — outline, circles, AND helical flute edges —
// as 3D line segments, independent of the surface mesh. Used for the end view.

function shapeToEdges(oc, shape) {
  let lineType = null;
  try { lineType = oc.GeomAbs_CurveType.GeomAbs_Line; } catch { lineType = null; }

  const seen = new Set();
  const exp = new oc.TopExp_Explorer_2(
    shape,
    oc.TopAbs_ShapeEnum.TopAbs_EDGE,
    oc.TopAbs_ShapeEnum.TopAbs_SHAPE
  );

  const edges = [];
  let maxLen = 0;

  for (; exp.More(); exp.Next()) {
    try {
      const edge = oc.TopoDS.Edge_1(exp.Current());
      const ad   = new oc.BRepAdaptor_Curve_2(edge);
      const f = ad.FirstParameter(), l = ad.LastParameter();
      if (!isFinite(f) || !isFinite(l) || l - f <= 1e-9) continue;

      let n = 48;
      try { n = ad.GetType() === lineType ? 1 : 64; } catch { n = 48; }

      const pts = [];
      for (let k = 0; k <= n; k++) {
        const t = f + ((l - f) * k) / n;
        const pt = ad.Value(t);
        pts.push([pt.X(), pt.Y(), pt.Z()]);
      }

      const a = pts[0], b = pts[pts.length - 1];
      const key = [a, b].map(pp => pp.map(v => v.toFixed(2)).join(',')).sort().join('|');
      if (seen.has(key)) continue;
      seen.add(key);

      let len = 0;
      for (let k = 0; k + 1 < pts.length; k++) {
        const dx = pts[k+1][0]-pts[k][0], dy = pts[k+1][1]-pts[k][1], dz = pts[k+1][2]-pts[k][2];
        len += Math.hypot(dx, dy, dz);
      }
      edges.push({ pts, len });
      if (len > maxLen) maxLen = len;
    } catch { /* skip degenerate */ }
  }

  const positions = [];
  const minLen = maxLen * 0.04;
  for (const e of edges) {
    if (e.len < minLen) continue;
    const { pts } = e;
    for (let k = 0; k + 1 < pts.length; k++) {
      positions.push(pts[k][0], pts[k][1], pts[k][2], pts[k+1][0], pts[k+1][1], pts[k+1][2]);
    }
  }

  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  return g;
}

// ─── Bounds helper ────────────────────────────────────────────────────────────

function boundsOf(segs) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [x1, y1, x2, y2] of segs) {
    minX = Math.min(minX, x1, x2); maxX = Math.max(maxX, x1, x2);
    minY = Math.min(minY, y1, y2); maxY = Math.max(maxY, y1, y2);
  }
  if (!isFinite(minX)) { minX = maxX = minY = maxY = 0; }
  return { segs, minX, maxX, minY, maxY };
}

// ─── End (front) view (port of occDxf.ts buildEndView) ───────────────────────
// Looks down the drill axis (-Z). Keeps only the outer-diameter circle and the
// tip-region edges (cutting lips + chisel), then projects to the XY plane.

function buildEndView(oc, shape, rBody, totalLen) {
  const tipZStart = totalLen * 0.80;
  const rTol      = rBody * 0.06;

  const geo = shapeToEdges(oc, shape);
  const pos = geo.getAttribute('position');
  const segs = [];
  if (!pos) return boundsOf(segs);

  for (let i = 0; i + 1 < pos.count; i += 2) {
    const x1 = pos.getX(i),   y1 = pos.getY(i),   z1 = pos.getZ(i);
    const x2 = pos.getX(i+1), y2 = pos.getY(i+1), z2 = pos.getZ(i+1);
    if (![x1,y1,z1,x2,y2,z2].every(v => isFinite(v) && !isNaN(v))) continue;

    const midZ = (z1 + z2) / 2;
    const midR = (Math.hypot(x1, y1) + Math.hypot(x2, y2)) / 2;

    const isOuterCircle = Math.abs(midR - rBody) < rTol;
    const isTipRegion   = midZ > tipZStart;
    if (!isOuterCircle && !isTipRegion) continue;

    const dx = x2 - x1, dy = y2 - y1;
    if (dx * dx + dy * dy < 0.01) continue;   // drop near-zero XY projection
    segs.push([x1, y1, x2, y2]);
  }

  return boundsOf(segs);
}

// ─── Write DXF with both side + end views (port of occDxf.ts layout) ─────────

function buildDxf(side, end) {
  const d = new Drawing();
  d.addLineType('CENTER', 'Center ____ _ ____ _', [12.7, -2.54, 2.54, -2.54]);
  d.addLayer('Visible', 7, 'CONTINUOUS');
  d.addLayer('Center',  1, 'CENTER');
  d.addLayer('Text',    3, 'CONTINUOUS');

  const MIN = 0.1 * 0.1;
  const draw = (segs, ox, oy) => {
    for (const [x1, y1, x2, y2] of segs) {
      const dx = x2 - x1, dy = y2 - y1;
      if (dx * dx + dy * dy < MIN) continue;
      d.drawLine(ox + x1, oy + y1, ox + x2, oy + y2);
    }
  };

  // Layout: side view at origin, end view to its right with a gap.
  const sideOx = -side.minX;
  const sideOy = -(side.minY + side.maxY) / 2;
  const gap    = Math.max(20, (end.maxX - end.minX) * 0.4);
  const endOx  = sideOx + side.maxX + gap - end.minX;
  const endOy  = -(end.minY + end.maxY) / 2;

  d.setActiveLayer('Visible');
  draw(side.segs, sideOx, sideOy);
  draw(end.segs,  endOx,  endOy);

  d.setActiveLayer('Center');
  // Side-view axis centreline
  d.drawLine(sideOx + side.minX - 6, 0, sideOx + side.maxX + 6, 0);
  // End-view crosshair
  const endR  = Math.max(end.maxX - end.minX, end.maxY - end.minY) / 2 + 6;
  const endCx = endOx + (end.minX + end.maxX) / 2;
  d.drawLine(endCx - endR, 0, endCx + endR, 0);
  d.drawLine(endCx, -endR, endCx, endR);

  d.setActiveLayer('Text');
  const labelY = Math.min(sideOy + side.minY, endOy + end.minY) - 8;
  d.drawText(sideOx + side.minX, labelY, 4, 0, 'SIDE VIEW');
  d.drawText(endOx + end.minX,   labelY, 4, 0, 'END VIEW');
  const Dc = p.cutting_diameter, OAL = p.overall_length;
  d.drawText(
    sideOx + side.minX, labelY - 7, 3, 0,
    `Drill  Dc${Dc}mm x OAL${OAL}mm  ${p.n_flutes || 2}-flute  ${p.helix_angle}deg helix  ${p.point_angle}deg point`
  );

  // ── Overall drawing bounds (for extents + the initial view) ──
  const allMinX = sideOx + side.minX;
  const allMaxX = endOx + end.maxX;
  const halfH   = Math.max(side.maxY - side.minY, end.maxY - end.minY) / 2;
  const allMinY = (labelY - 7) - 4;     // a little below the spec text line
  const allMaxY = halfH + 2;
  const W  = Math.max(allMaxX - allMinX, 1);
  const H  = Math.max(allMaxY - allMinY, 1);
  const cx = (allMinX + allMaxX) / 2;
  const cy = (allMinY + allMaxY) / 2;

  // ── Header: a SINGLE $ACADVER (the duplicate is what made AutoCAD open
  //    the file read-only), millimetre units, and real drawing extents so
  //    the drawing isn't flung off-screen / zoomed to nothing on open. ──
  d.header('ACADVER',  [[1, 'AC1021']]);                       // overwrite → no dup
  d.header('INSUNITS', [[70, 4]]);                             // 4 = millimetres
  d.header('EXTMIN',   [[10, allMinX], [20, allMinY], [30, 0]]);
  d.header('EXTMAX',   [[10, allMaxX], [20, allMaxY], [30, 0]]);
  d.header('LIMMIN',   [[10, allMinX], [20, allMinY]]);
  d.header('LIMMAX',   [[10, allMaxX], [20, allMaxY]]);

  // ── Frame the *ACTIVE viewport on the drawing (default is centred at the
  //    origin with height 1000 → everything looks tiny). View height is sized
  //    to cover the width on a typical widescreen without clipping. ──
  const vpTable = d.tables['VPORT'];
  if (vpTable && vpTable.elements && vpTable.elements.length) {
    const vp = vpTable.elements[0];
    vp.cx = cx; vp.cy = cy;
    vp.viewH  = Math.max(H, W * 0.62) * 1.12;
    vp.aspect = W / H;
    vp.tags = function (manager) {
      manager.push(0, 'VPORT');
      manager.push(5, this.handle);
      manager.push(330, this.ownerObjectHandle);
      manager.push(100, 'AcDbSymbolTableRecord');
      manager.push(100, 'AcDbViewportTableRecord');
      manager.push(2, this.name);
      manager.push(70, 0);
      manager.push(10, 0.0); manager.push(20, 0.0);            // vp lower-left
      manager.push(11, 1.0); manager.push(21, 1.0);            // vp upper-right
      manager.push(12, this.cx); manager.push(22, this.cy);    // view centre (DCS)
      manager.push(13, 0.0); manager.push(23, 0.0);            // snap base
      manager.push(14, 10.0); manager.push(24, 10.0);          // snap spacing
      manager.push(15, 10.0); manager.push(25, 10.0);          // grid spacing
      manager.push(16, 0.0); manager.push(26, 0.0); manager.push(36, 1.0); // view dir
      manager.push(17, 0.0); manager.push(27, 0.0); manager.push(37, 0.0); // target
      manager.push(40, this.viewH);                            // view height (zoom)
      manager.push(41, this.aspect);                           // aspect ratio
      manager.push(42, 50.0);                                  // lens length
      manager.push(43, 0.0); manager.push(44, 0.0);            // clip planes
      manager.push(50, 0.0); manager.push(51, 0.0);            // snap/view twist
      manager.push(71, 0); manager.push(72, 100);
      manager.push(73, 1); manager.push(74, 3);
      manager.push(75, 0); manager.push(76, 0);
      manager.push(77, 0); manager.push(78, 0);
    };
  }

  return d.toDxfString();
}

// ─── Main ─────────────────────────────────────────────────────────────────────

(async () => {
  process.stderr.write('[1/5] Bootstrapping OpenCASCADE…\n');
  const wasmBin = fs.readFileSync(path.join(occDir, 'opencascade.wasm'));
  const oc = await ocFactory({
    wasmBinary: wasmBin,
    locateFile: fn => path.join(occDir, fn),
  });

  await oc.loadDynamicLibrary(toUrl(path.join(occDir, 'opencascade.core.wasm')), DYLIB);
  await oc.loadDynamicLibrary(toUrl(path.join(occDir, 'opencascade.modelingAlgorithms.wasm')), DYLIB);
  process.stderr.write('[2/5] OCC ready — building solid…\n');

  const shape = buildSolid(oc);
  process.stderr.write('[3/5] Solid built — tessellating…\n');

  const geo = shapeToGeometry(oc, shape);
  process.stderr.write('[4/6] Mesh ready — projecting side view…\n');

  const visGeo  = projectSideView(geo);
  const side    = boundsOf(toSegments(visGeo));
  process.stderr.write(`[5/6] side: ${side.segs.length} segments — building end view…\n`);

  const rBody    = Math.max(p.cutting_diameter, 0.1) / 2;
  const totalLen = p.overall_length;
  const end      = buildEndView(oc, shape, rBody, totalLen);
  process.stderr.write(`[6/6] end: ${end.segs.length} segments — writing DXF…\n`);

  const dxf = buildDxf(side, end);
  fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });
  fs.writeFileSync(outPath, dxf, 'utf8');
  process.stdout.write(outPath + '\n');
  process.stderr.write(`✅  ${outPath}  (${dxf.length} bytes)\n`);
})().catch(e => {
  process.stderr.write('❌  FAIL: ' + (e?.message || String(e)) + '\n');
  process.exit(1);
});
