/**
 * gen_proposal.mjs
 * Usage: node gen_proposal.mjs <mesh.json> <segments.json>
 *
 * Produces two projected views of the drill:
 *   side  — project along world Y (classic side view, drill horizontal)
 *   front — project along world Z (end view from tip, shows cutting face)
 *
 * Output JSON: { side: [[z1,x1,z2,x2],...], front: [[x1,y1,x2,y2],...] }
 * where for side:  z=axial, x=radial
 * and  for front:  x=radial-X, y=radial-Y (circle of cutting diameter)
 */

import { readFileSync, writeFileSync } from 'fs';
import { BufferGeometry, Float32BufferAttribute, Mesh, Matrix4 } from 'three';
import { MeshBVH } from 'three-mesh-bvh';
import { ProjectionGenerator } from 'three-edge-projection';

const [,, meshPath, outPath] = process.argv;
if (!meshPath || !outPath) {
    console.error('Usage: node gen_proposal.mjs <mesh.json> <out.json>');
    process.exit(1);
}

const { v, i } = JSON.parse(readFileSync(meshPath, 'utf8'));

function buildGeometry(verts, indices, rotMat) {
    const geom = new BufferGeometry();
    const pos = new Float32Array(verts);
    geom.setAttribute('position', new Float32BufferAttribute(pos, 3));
    geom.setIndex(indices);
    if (rotMat) geom.applyMatrix4(rotMat);
    geom.computeVertexNormals();
    geom.boundsTree = new MeshBVH(geom);
    return geom;
}

function project(geometry) {
    const mesh = new Mesh(geometry);
    const gen = new ProjectionGenerator();
    gen.iterationTime = Infinity;
    const task = gen.generate(mesh);
    let step = { done: false };
    while (!step.done) step = task.next();
    return step.value.visibleEdges.getLineGeometry();
}

function extractSegs(lineGeom) {
    const pos = lineGeom.attributes.position.array;
    const segs = [];
    for (let k = 0; k < pos.length; k += 6) {
        const ax = pos[k],   az = pos[k+2];
        const bx = pos[k+3], bz = pos[k+5];
        if (Math.abs(bx-ax) > 1e-6 || Math.abs(bz-az) > 1e-6)
            segs.push([ax, az, bx, bz]);
    }
    return segs;
}

// ── Side view: project along +Y ──────────────────────────────────────────────
// Drill axis = Z. ProjectionGenerator looks along +Y.
// Output: (x, 0, z) → DXF (z=axial, x=radial)  ← handled in Python
const sideGeom = buildGeometry(v, i, null);
const sideLine = project(sideGeom);
// segs: [x1, z1, x2, z2] (raw from projection; Python swaps to [z1,x1,z2,x2])
const sideSegs = extractSegs(sideLine);

// ── Front view: project from TIP end (looking along +Z, tip→shank) ──────────
// Rx(+90°): [x,y,z] → [x, -z, y]  — tip (z≈0) lands at y≈0 (nearest camera),
// shank (z≈OAL) lands at y≈-OAL (far).  ProjectionGenerator looks from +Y so
// the tip face is the closest surface — we see cutting geometry, not shank.
// In projection plane: x'=orig_X, z'=orig_Y  →  DXF: (orig_X, orig_Y) ✓
const rotFront = new Matrix4().makeRotationX(Math.PI / 2);
const frontGeom = buildGeometry(v, i, rotFront);
const frontLine = project(frontGeom);
const frontRaw  = extractSegs(frontLine);
// Convert to (orig_X, orig_Y): with Rx(+90°), x'=x_orig, z'=y_orig
const frontSegs = frontRaw.map(([x1, z1, x2, z2]) => [x1, z1, x2, z2]);

writeFileSync(outPath, JSON.stringify({ side: sideSegs, front: frontSegs }));
console.log(`side: ${sideSegs.length}  front: ${frontSegs.length}`);
