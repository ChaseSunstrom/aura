  orbEngine(cv, getMode, o) {
    o = o || {};
    const ctx = cv.getContext('2d'), W = cv.width, H = cv.height, cx = W / 2, cy = H / 2;
    const S = Math.min(W, H) * (o.scale || .36), sc = W / 600;
    const N = o.n || 1500, ga = Math.PI * (3 - Math.sqrt(5)), F = [];
    for (let i = 0; i < N; i++) { const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = ga * i; F.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, hot: Math.random() < .03, life: Math.random(), sp: .6 + Math.random() * .8 }); }
    const RINGS = [
      { r: .56, seg: 12, a: 1.35, b: 0, dir: 1 },
      { r: .46, seg: 10, a: .2, b: .95, dir: -1 },
      { r: .36, seg: 8, a: .2, b: -.95, dir: 1.4 }
    ];
    const T = {
      idle: { spin: .18, gyro: .12, align: 0, shell: 1, swirl: 0, flow: .2, glow: .7, c: [63, 208, 232] },
      listen: { spin: .35, gyro: .05, align: 1, shell: 1.02, swirl: 0, flow: .3, glow: .9, c: [63, 208, 232] },
      speak: { spin: .45, gyro: .08, align: .65, shell: 1.02, swirl: 0, flow: .35, glow: 1.1, c: [140, 230, 252] },
      think: { spin: 1.6, gyro: 1.1, align: 0, shell: .84, swirl: 1.4, flow: 1.6, glow: .85, c: [155, 140, 255] },
      embed: { spin: .9, gyro: .5, align: .2, shell: .9, swirl: .4, flow: .9, glow: 1, c: [155, 140, 255] },
      music: { spin: .55, gyro: .25, align: .3, shell: 1.04, swirl: .2, flow: .5, glow: 1, c: [63, 208, 232] },
      alert: { spin: .05, gyro: 0, align: 1, shell: .96, swirl: 0, flow: .1, glow: .8, c: [255, 107, 139] },
      act: { spin: .9, gyro: .45, align: .15, shell: .94, swirl: .5, flow: .9, glow: .95, c: [63, 208, 232] },
      work: { spin: 1.1, gyro: .2, align: 0, shell: .9, swirl: .25, flow: 1.3, glow: 1, c: [90, 220, 245] }
    };
    const cur = JSON.parse(JSON.stringify(T.idle));
    let last = 0, spinA = 0, gyroA = 0, shellA = 0, flowA = 0, lastMode = '', kick = -1e9;
    const self = this, items = [];
    const poly = (pts, fill, stroke, lw) => { ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]); ctx.closePath(); if (fill) { ctx.fillStyle = fill; ctx.fill(); } if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw * sc; ctx.stroke(); } };
    const seg = (a, b, col, w) => { ctx.strokeStyle = col; ctx.lineWidth = w * sc; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
    const frame = (t) => {
      const dt = Math.min(50, t - (last || t)); last = t;
      const mode = getMode(), tg = T[mode] || T.idle;
      if (mode !== lastMode) { if (lastMode) kick = t; lastMode = mode; }
      if (mode === 'work' && t - kick > 1200) kick = t;
      for (const k of ['spin', 'gyro', 'align', 'shell', 'swirl', 'flow', 'glow']) cur[k] += (tg[k] - cur[k]) * .035;
      for (let i = 0; i < 3; i++) cur.c[i] += (tg.c[i] - cur.c[i]) * .05;
      const C = cur.c.map(Math.round).join(',');
      const Cd = cur.c.map((v) => Math.round(v * .16)).join(',');
      spinA += dt * .001 * cur.spin; gyroA += dt * .001 * cur.gyro; shellA += dt * .001 * (.12 + cur.flow * .25); flowA += dt * .001 * (.6 + cur.flow * 1.6);
      const beat = .5 + .5 * Math.sin(t * .008) * Math.sin(t * .0031);
      const amp = mode === 'listen' ? (.5 + .5 * Math.abs(Math.sin(t * .006) * Math.sin(t * .0017))) : mode === 'speak' ? beat : mode === 'music' ? Math.pow(Math.abs(Math.sin(t * .0065)), 3) : 0;
      const tl = .28, ct = Math.cos(tl), st = Math.sin(tl);
      const proj = (x, y, z) => { const y1 = y * ct - z * st, z1 = y * st + z * ct, s = 3.3 / (3.3 - z1); return [cx + x * S * s, cy + y1 * S * s, z1, s]; };
      items.length = 0;
      for (const p of F) {
        let k = cur.shell;
        if (mode === 'listen') k *= 1 + .07 * amp * Math.sin(p.y * 9 - t * .012);
        else if (mode === 'speak') k *= 1 + .07 * amp;
        else if (mode === 'music') k *= 1 + .09 * amp * (.5 + .5 * Math.sin(p.y * 5 + p.x * 3));
        else if (mode === 'embed') { p.life -= dt * .00045 * p.sp; if (p.life < 0) p.life += 1; k *= .25 + .75 * p.life; }
        else if (mode === 'alert') k *= 1 + .012 * Math.sin(t * .05 + p.x * 40);
        else k *= 1 + .018 * Math.sin(t * .002 + p.y * 3);
        const a = shellA * p.sp * (1 + cur.swirl * p.y * p.y) + cur.swirl * p.y * .8, ca = Math.cos(a), sa = Math.sin(a);
        const q = proj((p.x * ca + p.z * sa) * k, p.y * k, (-p.x * sa + p.z * ca) * k);
        items.push([q[2], 0, q[0], q[1], p.hot, q[3]]);
      }
      const pRot = (x, y, z, a, b) => { const y1 = y * Math.cos(a) - z * Math.sin(a), z1 = y * Math.sin(a) + z * Math.cos(a); return [x * Math.cos(b) + z1 * Math.sin(b), y1, -x * Math.sin(b) + z1 * Math.cos(b)]; };
      RINGS.forEach((R, ri) => {
        if (o.rings && ri >= o.rings) return;
        const wk = mode === 'work' ? 1 : 0;
        const a = R.a * (1 - cur.align) + Math.sin(t * .0003 + ri) * .12 * (1 - cur.align) + wk * Math.sin(t * .0016 + ri * 2.1) * .7;
        const b = (R.b + gyroA * R.dir) * (1 - cur.align) + (ri - 1) * .08 * cur.align + wk * Math.cos(t * .0012 + ri * 2.1) * .7;
        const P = (x, y, z) => { const q = pRot(x, y, z, a, b); return proj(q[0], q[1], q[2]); };
        const pulseR = 1 + (mode === 'speak' || mode === 'music' ? .05 * amp : 0);
        const r0 = (R.r - .07) * pulseR, r1 = (R.r + .05) * pulseR, h = .05, gap = .1;
        const arc = (rr, zz, f, e, n) => { const out = []; for (let i = 0; i <= n; i++) { const an = f + (e - f) * i / n; out.push(P(Math.cos(an) * rr, Math.sin(an) * rr, zz)); } return out; };
        for (let k = 0; k < R.seg; k++) {
          const s0 = spinA * R.dir + k / R.seg * 6.283 + gap / 2, s1 = spinA * R.dir + (k + 1) / R.seg * 6.283 - gap / 2, sm = (s0 + s1) / 2;
          const fo = arc(r1, h, s0, s1, 6), fi = arc(r0, h, s1, s0, 6), bo = arc(r1, -h, s0, s1, 6), bi = arc(r0, -h, s1, s0, 6);
          const zf = P(Math.cos(sm) * R.r, Math.sin(sm) * R.r, h)[2], zb = P(Math.cos(sm) * R.r, Math.sin(sm) * R.r, -h)[2];
          const lit = mode === 'music' ? (.6 + .8 * Math.max(0, Math.sin(t * .013 + k * 1.3))) : mode === 'think' ? (.7 + .6 * Math.max(0, Math.sin(t * .02 - k))) : 1;
          const wind = []; if (!o.mini) for (let j = 1; j < 6; j++) { const an = s0 + (s1 - s0) * j / 6; wind.push([P(Math.cos(an) * (r0 + .012), Math.sin(an) * (r0 + .012), h * (zf > zb ? 1 : -1)), P(Math.cos(an) * (r1 - .012), Math.sin(an) * (r1 - .012), h * (zf > zb ? 1 : -1))]); }
          const led = P(Math.cos(sm) * (r1 + .004), Math.sin(sm) * (r1 + .004), 0);
          const capA = [fo[0], fi[fi.length - 1], bi[bi.length - 1], bo[0]], capB = [fo[fo.length - 1], fi[0], bi[0], bo[bo.length - 1]];
          items.push([(zf + zb) / 2, 1, { fo, fi, bo, bi, front: zf > zb, lit, wind, led, capA, capB }]);
        }
        // strut to the core
        if (ri === 0 && !o.mini) for (let k = 0; k < 4; k++) { const an = spinA * R.dir + k / 4 * 6.283 + .02, p1 = P(Math.cos(an) * r0, Math.sin(an) * r0, 0), p2 = P(Math.cos(an) * .2, Math.sin(an) * .2, 0); items.push([(p1[2] + p2[2]) / 2, 4, p1, p2]); }
        items.push([P(0, 0, 0)[2] - .02 - .001 * ri, 2, P, R.r, ri]);
        // data flowing along each ring
        if (!o.mini) for (let i = 0; i < 26; i++) { const an = flowA * R.dir * .8 + i / 26 * 6.283 + ri, q = P(Math.cos(an) * (R.r + .1), Math.sin(an) * (R.r + .1), 0), q2 = P(Math.cos(an - .07 * R.dir) * (R.r + .1), Math.sin(an - .07 * R.dir) * (R.r + .1), 0); items.push([q[2], 5, q, q2]); }
      });
      items.push([0, 3]);
      items.sort((m, n) => m[0] - n[0]);
      ctx.clearRect(0, 0, W, H);
      const sinceKick = t - kick;
      for (const it of items) {
        const d = (it[0] + 1) / 2;
        if (it[1] === 0) {
          ctx.globalCompositeOperation = 'lighter';
          ctx.fillStyle = (it[4] ? 'rgba(255,190,90,' : 'rgba(' + C + ',') + (.035 + d * .62).toFixed(3) + ')';
          const z = (.45 + d * 1.7) * sc * (it[4] ? 1.4 : 1); ctx.fillRect(it[2] - z / 2, it[3] - z / 2, z, z);
        } else if (it[1] === 1) {
          const B = it[2], L = B.lit, al = (.4 + d * .6) * L;
          const near = B.front ? [B.bo.concat(B.bi), 'b'] : [B.fo.concat(B.fi), 'f'];
          const farF = B.front ? B.bo.concat(B.bi) : B.fo.concat(B.fi), nearF = B.front ? B.fo.concat(B.fi) : B.bo.concat(B.bi);
          // solid body so it hides what's behind it
          ctx.globalCompositeOperation = 'source-over';
          poly(farF, 'rgba(' + Cd + ',.95)');
          poly(B.fo.concat(B.bo.slice().reverse()), 'rgba(' + Cd + ',.98)');
          poly(B.fi.concat(B.bi.slice().reverse()), 'rgba(2,8,14,.98)');
          poly(B.capA, 'rgba(' + Cd + ',.98)'); poly(B.capB, 'rgba(' + Cd + ',.98)');
          poly(nearF, 'rgba(' + Cd + ',1)');
          ctx.globalCompositeOperation = 'lighter';
          poly(B.fo.concat(B.bo.slice().reverse()), 'rgba(' + C + ',' + (.1 * al).toFixed(3) + ')', 'rgba(' + C + ',' + (.3 * al).toFixed(3) + ')', .8);
          poly(nearF, 'rgba(' + C + ',' + (.14 * al).toFixed(3) + ')', 'rgba(' + (L > 1.2 ? '235,252,255' : C) + ',' + Math.min(1, .95 * al).toFixed(3) + ')', 1.4);
          for (const w of B.wind) seg(w[0], w[1], 'rgba(205,246,255,' + (.45 * al).toFixed(3) + ')', 1);
          const lr = 3.2 * sc * (.7 + .5 * d), lg = ctx.createRadialGradient(B.led[0], B.led[1], 0, B.led[0], B.led[1], lr * 3);
          lg.addColorStop(0, 'rgba(240,253,255,' + Math.min(1, al).toFixed(2) + ')'); lg.addColorStop(.35, 'rgba(' + C + ',' + (.6 * al).toFixed(2) + ')'); lg.addColorStop(1, 'rgba(' + C + ',0)');
          ctx.fillStyle = lg; ctx.beginPath(); ctx.arc(B.led[0], B.led[1], lr * 3, 0, 6.283); ctx.fill();
        } else if (it[1] === 2) {
          ctx.globalCompositeOperation = 'lighter';
          const P = it[2], r = it[3]; let pv = null;
          for (let i = 0; i <= 90; i++) { const an = i / 90 * 6.283, q = P(Math.cos(an) * (r + .1), Math.sin(an) * (r + .1), 0); if (pv) seg(pv, q, 'rgba(' + C + ',' + (.06 + ((q[2] + 1) / 2) * .22).toFixed(3) + ')', 1); pv = q; }
          pv = null; const sw = t * .0012 * (1 + cur.spin) + it[4] * 2;
          for (let i = 0; i <= 14; i++) { const an = sw + i * .04, q = P(Math.cos(an) * (r + .1), Math.sin(an) * (r + .1), 0); if (pv) seg(pv, q, 'rgba(240,253,255,' + (i / 14).toFixed(2) + ')', 2.4); pv = q; }
        } else if (it[1] === 4) {
          ctx.globalCompositeOperation = 'source-over'; seg(it[2], it[3], 'rgba(' + Cd + ',1)', 5);
          ctx.globalCompositeOperation = 'lighter'; seg(it[2], it[3], 'rgba(' + C + ',' + (.25 + d * .4).toFixed(3) + ')', 1.2);
        } else if (it[1] === 5) {
          ctx.globalCompositeOperation = 'lighter'; seg(it[2], it[3], 'rgba(220,250,255,' + (.15 + d * .75).toFixed(3) + ')', 1.6 + d);
        } else {
          ctx.globalCompositeOperation = 'lighter';
          const flick = mode === 'alert' ? (.8 + .2 * Math.sin(t * .03)) : 1;
          const g0 = cur.glow * flick * (1 + .25 * amp) + (sinceKick < 500 ? .6 * (1 - sinceKick / 500) : 0);
          const r0 = S * .15 * (1 + .12 * amp);
          const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r0 * 2.4);
          g.addColorStop(0, 'rgba(245,254,255,' + Math.min(1, g0).toFixed(3) + ')'); g.addColorStop(.22, 'rgba(180,244,252,' + Math.min(1, .9 * g0).toFixed(3) + ')'); g.addColorStop(.5, 'rgba(' + C + ',' + (.4 * g0).toFixed(3) + ')'); g.addColorStop(1, 'rgba(' + C + ',0)');
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, r0 * 2.4, 0, 6.283); ctx.fill();
          // small wire sphere inside the core
          if (!o.mini) for (let k = 0; k < 3; k++) { const an = t * .0009 * (1 + cur.spin) + k * 1.05; ctx.strokeStyle = 'rgba(235,252,255,.35)'; ctx.lineWidth = sc; ctx.beginPath(); ctx.ellipse(cx, cy, S * .1 * Math.abs(Math.cos(an)) + .5, S * .1, 0, 0, 6.283); ctx.stroke(); }
          if (sinceKick < 900) { const f = sinceKick / 900; ctx.strokeStyle = 'rgba(' + C + ',' + (.8 * (1 - f)).toFixed(3) + ')'; ctx.lineWidth = (6 - 5 * f) * sc; ctx.beginPath(); ctx.arc(cx, cy, S * (.2 + 1.1 * f), 0, 6.283); ctx.stroke(); }
        }
      }
      ctx.globalCompositeOperation = 'source-over';
      self.rafs = self.rafs || {}; self.rafs[o.key || 'orb'] = requestAnimationFrame(frame);
    };
    this.rafs = this.rafs || {}; this.rafs[o.key || 'orb'] = requestAnimationFrame(frame);
  }
