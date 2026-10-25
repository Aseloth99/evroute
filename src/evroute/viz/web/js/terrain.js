/* EVRoute viewer -- terrain.js
 *
 * A shaded-relief background computed in the browser from the node
 * elevations only (no tiles, no external data):
 *
 * 1. Interpolation.  Elevation on a coarse regular grid (about 420 cells on
 *    the long side) by inverse-distance weighting (Shepard 1968) in its
 *    local, "modified" form with the smooth Franke-Little weights (Franke &
 *    Nielson 1980), using a bucket grid for the radius queries.
 * 2. Hillshade.  Lambertian shading of the interpolated surface: the surface
 *    normal from central differences is dotted with a light from the
 *    north-west (azimuth 315 deg, altitude 45 deg), the classic cartographic
 *    convention.  Because the grid is coarse (kilometres per cell) the
 *    relief is vertically exaggerated by a factor chosen from the 90th
 *    percentile of the slope, so flat and mountainous scenarios both read.
 * 3. Hypsometric tint.  A restrained colour ramp by elevation (lowland green
 *    to upland tan to high-mountain grey), multiplied by the shading.
 * 4. Mask.  Cells far from every node fade out, which outlines the region
 *    the network covers (for the Turkey case study: the country).
 *
 * The result is a small canvas in world coordinates that the map draws
 * scaled with image smoothing; it is rebuilt only when the theme changes.
 * Node elevations only exist along roads, so this is a *suggestion* of the
 * relief, not a DEM: when the map is zoomed in beyond the grid resolution it
 * fades to flat land.
 *
 * References: D. Shepard (1968), A two-dimensional interpolation function
 * for irregularly-spaced data, Proc. 23rd ACM National Conference, 517-524.
 * R. Franke, G. Nielson (1980), Smooth interpolation of large sets of
 * scattered data, Int. J. Numerical Methods in Engineering 15(11), 1691-1704.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;

  const RAMP_LIGHT = [
    [-100, [226, 236, 218]], [0, [224, 235, 214]], [250, [229, 236, 210]], [550, [236, 234, 208]],
    [900, [238, 226, 198]], [1300, [232, 214, 186]], [1800, [222, 200, 176]], [2400, [214, 198, 186]],
    [3200, [238, 234, 230]],
  ];
  const RAMP_DARK = [
    [-100, [22, 32, 40]], [0, [23, 34, 42]], [250, [26, 37, 45]], [550, [31, 41, 49]],
    [900, [37, 45, 53]], [1300, [43, 49, 57]], [1800, [49, 54, 62]], [2400, [57, 61, 69]],
    [3200, [74, 78, 86]],
  ];

  function ramp(stops, z) {
    if (z <= stops[0][0]) return stops[0][1];
    for (let i = 1; i < stops.length; i++) {
      if (z <= stops[i][0]) {
        const a = stops[i - 1], b = stops[i];
        const t = (z - a[0]) / (b[0] - a[0]);
        return [a[1][0] + (b[1][0] - a[1][0]) * t, a[1][1] + (b[1][1] - a[1][1]) * t, a[1][2] + (b[1][2] - a[1][2]) * t];
      }
    }
    return stops[stops.length - 1][1];
  }

  /**
   * Interpolated elevation grid {z, dmin, gw, gh, bbox, cw, ch, maskR}, cached
   * on the model.  Modified Shepard interpolation with the Franke-Little
   * weights w = ((R - d)+ / (R d))^2 (Franke & Nielson 1980): only nodes
   * closer than R contribute and their weight falls smoothly to zero at R,
   * so the surface has no seams (plain k-nearest IDW jumps whenever the set
   * of neighbours changes).  R is at least the mask radius, so every visible
   * cell has data, and large enough to hold ~40 nodes on average.
   */
  function elevationGrid(model) {
    if (model._terrainGrid) return model._terrainGrid;
    const n = model.n, X = model.X, Y = model.Y, Z = model.Z;
    const [bx0, by0, bx1, by1] = model.bbox;
    const W0 = Math.max(bx1 - bx0, 1), H0 = Math.max(by1 - by0, 1);
    const margin = 0.08 * Math.max(W0, H0);
    const x0 = bx0 - margin, y0 = by0 - margin, x1 = bx1 + margin, y1 = by1 + margin;
    const W = x1 - x0, H = y1 - y0;
    const LONG = n > 15000 ? 280 : 420;
    const gw = W >= H ? LONG : Math.max(48, Math.round(LONG * W / H));
    const gh = W >= H ? Math.max(48, Math.round(LONG * H / W)) : LONG;
    const cw = W / gw, ch = H / gh;

    const maskR = Math.max(4.5 * model.meanEdge, 0.05 * Math.hypot(W, H));
    const density = n / Math.max(W0 * H0, 1);
    const R = Math.max(maskR, Math.sqrt(40 / (Math.PI * Math.max(density, 1e-12))));
    const R2 = R * R;

    // buckets of side R: every node within R of a cell is in the 3x3 block
    const BX = Math.max(1, Math.ceil(W / R)), BY = Math.max(1, Math.ceil(H / R));
    const start = new Int32Array(BX * BY + 1);
    const bucketOf = new Int32Array(n);
    for (let i = 0; i < n; i++) {
      const bx = U.clamp(Math.floor((X[i] - x0) / R), 0, BX - 1);
      const by = U.clamp(Math.floor((Y[i] - y0) / R), 0, BY - 1);
      bucketOf[i] = by * BX + bx;
      start[bucketOf[i] + 1]++;
    }
    for (let b = 1; b <= BX * BY; b++) start[b] += start[b - 1];
    const fill = start.slice(0, BX * BY);
    const items = new Int32Array(n);
    for (let i = 0; i < n; i++) items[fill[bucketOf[i]]++] = i;

    let zsum = 0;
    for (let i = 0; i < n; i++) zsum += Z[i];
    const zmean = n ? zsum / n : 0;
    const dEps = 0.5 * Math.min(cw, ch);
    const z = new Float32Array(gw * gh);
    const dmin = new Float32Array(gw * gh);
    for (let gy = 0; gy < gh; gy++) {
      const cy = y1 - (gy + 0.5) * ch;            // row 0 is the north edge
      const byc = U.clamp(Math.floor((cy - y0) / R), 0, BY - 1);
      for (let gx = 0; gx < gw; gx++) {
        const cx = x0 + (gx + 0.5) * cw;
        const bxc = U.clamp(Math.floor((cx - x0) / R), 0, BX - 1);
        let sw = 0, sz = 0, best = Infinity;
        for (let by = Math.max(0, byc - 1); by <= Math.min(BY - 1, byc + 1); by++) {
          for (let bx = Math.max(0, bxc - 1); bx <= Math.min(BX - 1, bxc + 1); bx++) {
            const b = by * BX + bx;
            for (let q = start[b]; q < start[b + 1]; q++) {
              const i = items[q];
              const dx = X[i] - cx, dy = Y[i] - cy;
              const d2 = dx * dx + dy * dy;
              if (d2 < best) best = d2;
              if (d2 >= R2) continue;
              const d = Math.sqrt(d2);
              const f = (R - d) / (R * Math.max(d, dEps));
              const w = f * f;
              sw += w;
              sz += w * Z[i];
            }
          }
        }
        const k = gy * gw + gx;
        z[k] = sw > 0 ? sz / sw : zmean;
        dmin[k] = Math.sqrt(best);
      }
    }
    model._terrainGrid = { z, dmin, gw, gh, bbox: [x0, y0, x1, y1], cw, ch, maskR };
    return model._terrainGrid;
  }

  /** Render the shaded relief for `theme`; returns {canvas, bbox}. */
  function build(model, theme) {
    const g = elevationGrid(model);
    const { z, dmin, gw, gh, cw, ch } = g;
    const [x0, y0, x1, y1] = g.bbox;

    // gradient magnitudes -> vertical exaggeration from the 90th percentile
    const grads = [];
    for (let gy = 1; gy < gh - 1; gy += 2) {
      for (let gx = 1; gx < gw - 1; gx += 2) {
        const k = gy * gw + gx;
        const dzdx = (z[k + 1] - z[k - 1]) / (2 * cw);
        const dzdy = (z[k - gw] - z[k + gw]) / (2 * ch);
        grads.push(Math.hypot(dzdx, dzdy));
      }
    }
    grads.sort((a, b) => a - b);
    const p90 = grads.length ? grads[Math.floor(grads.length * 0.9)] : 0;
    const exag = p90 > 0 ? U.clamp(0.5 / p90, 1, 400) : 1;

    // light from the north-west, 45 degrees above the horizon
    const az = 315 * Math.PI / 180, alt = 45 * Math.PI / 180;
    const lx = Math.sin(az) * Math.cos(alt), ly = Math.cos(az) * Math.cos(alt), lz = Math.sin(alt);

    const dark = theme.dark;
    const stops = dark ? RAMP_DARK : RAMP_LIGHT;
    const strength = dark ? 0.7 : 0.55;
    const R = g.maskR;
    const land = U.parseColor(theme.mapLand);

    const canvas = document.createElement('canvas');
    canvas.width = gw;
    canvas.height = gh;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(gw, gh);
    const px = img.data;
    const flatImg = ctx.createImageData(gw, gh);
    const fx = flatImg.data;
    for (let gy = 0; gy < gh; gy++) {
      for (let gx = 0; gx < gw; gx++) {
        const k = gy * gw + gx;
        const kl = gx > 0 ? k - 1 : k, kr = gx < gw - 1 ? k + 1 : k;
        const ku = gy > 0 ? k - gw : k, kd = gy < gh - 1 ? k + gw : k;
        const dzdx = (z[kr] - z[kl]) / ((kr - kl) * cw || cw);
        const dzdy = (z[ku] - z[kd]) / (((kd - ku) / gw) * ch || ch);
        // unit normal of (x east, y north, z up) surface
        let nx = -exag * dzdx, ny = -exag * dzdy;
        const nl = Math.sqrt(nx * nx + ny * ny + 1);
        const shade = Math.max(0, (nx * lx + ny * ly + lz) / nl) / lz;   // 1 on flat ground
        const f = 1 + (shade - 1) * strength;
        const c = ramp(stops, z[k]);
        const a = 1 - U.smoothstep(R * 0.45, R, dmin[k]);
        const o = 4 * k;
        px[o] = U.clamp(c[0] * f, 0, 255);
        px[o + 1] = U.clamp(c[1] * f, 0, 255);
        px[o + 2] = U.clamp(c[2] * f, 0, 255);
        px[o + 3] = Math.round(255 * a);
        fx[o] = land[0]; fx[o + 1] = land[1]; fx[o + 2] = land[2]; fx[o + 3] = px[o + 3];
      }
    }
    ctx.putImageData(img, 0, 0);
    // the same mask in flat land colour (shown when zoomed in far)
    const flat = document.createElement('canvas');
    flat.width = gw;
    flat.height = gh;
    flat.getContext('2d').putImageData(flatImg, 0, 0);
    return { canvas, flat, bbox: [x0, y0, x1, y1], cell: Math.max(cw, ch) };
  }

  EVR.terrain = { build, elevationGrid };
})(window.EVR = window.EVR || {});
