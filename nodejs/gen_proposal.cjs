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

// ─── Write DXF via dxf-writer ─────────────────────────────────────────────────

function buildDxf(segs) {
  const d = new Drawing();
  d.addLineType('CENTER', 'Center ____ _ ____ _', [12.7, -2.54, 2.54, -2.54]);
  d.addLayer('Visible', 7, 'CONTINUOUS');
  d.addLayer('Center',  1, 'CENTER');
  d.addLayer('Text',    3, 'CONTINUOUS');

  // Compute bounds for centering and centreline
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [x1, y1, x2, y2] of segs) {
    minX = Math.min(minX, x1, x2); maxX = Math.max(maxX, x1, x2);
    minY = Math.min(minY, y1, y2); maxY = Math.max(maxY, y1, y2);
  }
  if (!isFinite(minX)) { minX = maxX = minY = maxY = 0; }

  const ox  = -minX;
  const oy  = -(minY + maxY) / 2;
  const MIN = 0.1 * 0.1;

  d.setActiveLayer('Visible');
  for (const [x1, y1, x2, y2] of segs) {
    const dx = x2-x1, dy = y2-y1;
    if (dx*dx + dy*dy < MIN) continue;
    d.drawLine(ox+x1, oy+y1, ox+x2, oy+y2);
  }

  d.setActiveLayer('Center');
  d.drawLine(ox+minX - 6, 0, ox+maxX + 6, 0);

  d.setActiveLayer('Text');
  const Dc  = p.cutting_diameter, OAL = p.overall_length;
  const lbl = `PROPOSAL  Dc${Dc}mm x OAL${OAL}mm  ${p.n_flutes || 2}-flute  ${p.helix_angle}deg helix  ${p.point_angle}deg point`;
  d.drawText(ox + minX, oy + minY - 8, 3.5, 0, lbl);

  // Inject $ACADVER so AutoCAD opens as editable
  let raw = d.toDxfString();
  const ver = '9\n$ACADVER\n  1\nAC1009\n';
  if (raw.includes('  0\nSECTION\n  2\nHEADER\n')) {
    raw = raw.replace('  0\nSECTION\n  2\nHEADER\n', '  0\nSECTION\n  2\nHEADER\n  ' + ver);
  } else if (raw.includes('0\nSECTION\n2\nHEADER\n')) {
    raw = raw.replace('0\nSECTION\n2\nHEADER\n', '0\nSECTION\n2\nHEADER\n' + ver);
  }
  return raw;
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
  process.stderr.write('[4/5] Mesh ready — projecting side view…\n');

  const visGeo = projectSideView(geo);
  const segs   = toSegments(visGeo);
  process.stderr.write(`[5/5] ${segs.length} segments — writing DXF…\n`);

  const dxf = buildDxf(segs);
  fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });
  fs.writeFileSync(outPath, dxf, 'utf8');
  process.stdout.write(outPath + '\n');
  process.stderr.write(`✅  ${outPath}  (${dxf.length} bytes)\n`);
})().catch(e => {
  process.stderr.write('❌  FAIL: ' + (e?.message || String(e)) + '\n');
  process.exit(1);
});
