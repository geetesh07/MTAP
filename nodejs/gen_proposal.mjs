/**
 * gen_proposal.mjs
 * Usage: node gen_proposal.mjs <mesh.json> <segments.json>
 *
 * Reads a tessellated drill mesh (flat float vertex + int index arrays),
 * projects along Y with three-edge-projection (ProjectionGenerator),
 * and writes visible edge segments as JSON: [[z1,x1,z2,x2], ...]
 *
 * Coordinate convention (matches Python OCC build):
 *   Drill axis  = world Z (tip at 0, shank at OAL)
 *   Projection  = along world Y  →  visible plane is XZ
 *   DXF output  = X:axial(Z)  Y:radial(X)
 */

import { readFileSync, writeFileSync } from 'fs';
import { BufferGeometry, Float32BufferAttribute, Mesh } from 'three';
import { MeshBVH } from 'three-mesh-bvh';
import { ProjectionGenerator } from 'three-edge-projection';

const [,, meshPath, outPath] = process.argv;
if (!meshPath || !outPath) {
    console.error('Usage: node gen_proposal.mjs <mesh.json> <out.json>');
    process.exit(1);
}

const { v, i } = JSON.parse(readFileSync(meshPath, 'utf8'));

// Build BufferGeometry
const geometry = new BufferGeometry();
geometry.setAttribute('position', new Float32BufferAttribute(new Float32Array(v), 3));
geometry.setIndex(i);
geometry.computeVertexNormals();
geometry.boundsTree = new MeshBVH(geometry);

const mesh = new Mesh(geometry);

// Run projection synchronously (iterationTime=Infinity means it never yields mid-batch)
const gen = new ProjectionGenerator();
gen.iterationTime = Infinity;

const task = gen.generate(mesh);
let step = { done: false };
while (!step.done) {
    step = task.next();
}
const result = step.value;   // ProjectionResult { visibleEdges, hiddenEdges }

// Extract line segments from visible edges
// getLineGeometry() returns BufferGeometry with positions: [x1,0,z1, x2,0,z2, ...]
const lineGeom = result.visibleEdges.getLineGeometry();
const pos = lineGeom.attributes.position.array;

const segs = [];
for (let k = 0; k < pos.length; k += 6) {
    const x1 = pos[k],   z1 = pos[k+2];
    const x2 = pos[k+3], z2 = pos[k+5];
    // Skip degenerate zero-length segments
    if (Math.abs(x2-x1) > 1e-6 || Math.abs(z2-z1) > 1e-6) {
        segs.push([z1, x1, z2, x2]);   // [axial1, radial1, axial2, radial2]
    }
}

writeFileSync(outPath, JSON.stringify(segs));
console.log(`${segs.length} segments`);
