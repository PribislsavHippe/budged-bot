/* Вода для мини-приложения: вкладки-таблетка и календарь-стекло с каплей периода.
   Один шейдер WebGL (порт Nimbus UI · Liquid Navbar на чистый JS, без зависимостей); рисует только внутри своей панели,
   стоит на паузе вне экрана и при скрытой вкладке. Без WebGL остаются стекло и простая CSS-капля, физика не нужна.
   Вкладки: tabs(nav) — следит за aria-selected и hidden у [role=tab], сам ничего не переключает.
   Календарь: cal(panel).set([{row,x0,x1,y,rad}]) — по капле на строку календаря. */
(function () {
  'use strict';
  var ROWS = 6;
  var PAD = 14; // запас холста вокруг панели под тени и круги на воде

  var VERT = 'attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}';
  var FRAG = 'precision highp float;\n' +
    'uniform vec2 uRes;uniform float uDpr;uniform float uTime;uniform vec4 uPill;\n' +
    'uniform vec4 uBlob[6];uniform float uSy[6];uniform vec4 uRip[6];uniform vec3 uMouse;uniform float uDark;uniform float uAmb;uniform float uK;\n' +
    'float sdRB(vec2 p,vec2 b,float r){vec2 q=abs(p)-b+r;return length(max(q,0.))+min(max(q.x,q.y),0.)-r;}\n' +
    'float sdCap(vec2 p,vec4 B,float sy){p-=B.xy;p.y/=sy;p.x=max(abs(p.x)-B.z,0.);return(length(p)-B.w)*min(1.,sy);}\n' +
    'float smin(float a,float b,float k){float h=clamp(.5+.5*(b-a)/k,0.,1.);return mix(b,a,h)-k*h*(1.-h);}\n' +
    'float dropD(vec2 p){float d=1e4;for(int i=0;i<6;i++){if(uBlob[i].w>.5){d=smin(d,sdCap(p,uBlob[i],uSy[i]),uK);}}return d;}\n' +
    'float pillD(vec2 p){vec2 h=uPill.zw*.5;return sdRB(p-uPill.xy-h,h,min(h.y,28.));}\n' +
    'float lens(float d,float w){float t=clamp(-d/w,0.,1.);return sqrt(1.-(1.-t)*(1.-t));}\n' +
    'float waves(vec2 p){float w=0.;\n' +
    ' for(int i=0;i<6;i++){vec4 R=uRip[i];if(R.w>0.){float x=length(p-R.xy)-R.z*240.;w+=R.w*exp(-R.z*1.7)*sin(x*.3)*exp(-x*x/700.);}}\n' +
    ' vec2 m=p-uMouse.xy;w+=uMouse.z*3.6*exp(-dot(m,m)/1000.);\n' +
    ' w+=uAmb*(sin(p.x*.05+uTime*1.2)*.45+sin(p.x*.13-uTime*.85+p.y*.2)*.25);return w;}\n' +
    'float H(vec2 p){float dp=pillD(p);return lens(dp,min(uPill.w*.36,22.))*2.4+lens(dropD(p),11.)*7.5+waves(p)*smoothstep(0.,7.,-dp);}\n' +
    'float caus(vec2 p,float t){vec2 q=p;float c=0.;for(int i=0;i<3;i++){q+=vec2(sin(q.y*1.1+t),cos(q.x*1.3-t*.7))*.6;c+=1./(1.+16.*abs(sin(q.x)*sin(q.y)));}return c/3.;}\n' +
    'void main(){\n' +
    ' vec2 p=vec2(gl_FragCoord.x,uRes.y*uDpr-gl_FragCoord.y)/uDpr;\n' +
    ' float dp=pillD(p);float dd=dropD(p);\n' +
    ' float h0=H(p);vec3 n=normalize(vec3(h0-H(p+vec2(1.,0.)),h0-H(p+vec2(0.,1.)),1.));\n' +
    ' vec3 V=vec3(0.,0.,1.);\n' +
    ' float spec=pow(max(dot(reflect(-normalize(vec3(-.45,-.8,.85)),n),V),0.),70.);\n' +
    ' float spec2=pow(max(dot(reflect(-normalize(vec3(.6,.75,.7)),n),V),0.),22.)*.3;\n' +
    ' float fres=clamp((1.-n.z)*4.,0.,1.);\n' +
    ' vec3 irid=.5+.5*cos(6.2831*(vec3(0.,.33,.67)+fres*.85+p.x*.0028+uTime*.035));\n' +
    ' float pm=smoothstep(1.,-1.,dp);float dm=smoothstep(1.,-1.,dd)*smoothstep(1.,-2.,dp);\n' +
    ' float sh=smoothstep(9.,-9.,dropD(p-vec2(0.,5.)))*(1.-dm)*pm*(.11+.07*uDark);\n' +
    ' vec3 c=vec3(0.,.14,.36)*sh;float a=sh;\n' +
    ' float pa=clamp(pm*(fres*.5+spec*.9+spec2),0.,1.);\n' +
    ' c=mix(vec3(1.),irid,.5*fres)*pa+c*(1.-pa);a=pa+a*(1.-pa);\n' +
    ' float th=lens(dd,11.);\n' +
    ' float cs=caus((p+n.xy*th*18.)*.085,uTime*.9);\n' +
    ' vec3 tint=mix(vec3(.80,.95,1.),vec3(.55,.82,1.),uDark);\n' +
    ' float rim=dm*(1.-smoothstep(0.,8.,-dd));\n' +
    ' float cres=dm*smoothstep(.12,.55,dot(n.xy,normalize(vec2(.55,.85))))*smoothstep(0.,3.,-dd);\n' +
    ' float body=.5+.5*clamp(-n.y*3.,-1.,1.);\n' +
    ' float da=dm*(.26+.14*body)*(1.-.35*uDark);vec3 dc=mix(tint,vec3(1.),body*.6);\n' +
    ' float la=rim*.22;vec3 lc=mix(vec3(0.,.42,.78),vec3(.1,.55,.95),uDark);dc=lc*la+dc*da*(1.-la);da=la+da*(1.-la);\n' +
    ' la=clamp(cres*.7+smoothstep(.6,1.,cs)*.28*th*dm,0.,1.);dc=vec3(1.)*la+dc*(1.-la);da=la+da*(1.-la);\n' +
    ' la=clamp(dm*(spec*1.2+spec2*.5),0.,1.);dc=vec3(1.)*la+dc*(1.-la);da=la+da*(1.-la);\n' +
    ' la=dm*smoothstep(-2.,0.,dd)*.4;dc=mix(vec3(1.),irid,.35)*la+dc*(1.-la);da=la+da*(1.-la);\n' +
    ' c=dc+c*(1.-da);a=da+a*(1.-da);\n' +
    ' gl_FragColor=vec4(c,a);\n' +
    '}';

  function initGL(canvas) {
    var gl = canvas.getContext('webgl', { premultipliedAlpha: true, alpha: true, antialias: false });
    if (!gl) return null;
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
    }
    var vs = sh(gl.VERTEX_SHADER, VERT), fs = sh(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return null;
    var p = gl.createProgram();
    gl.attachShader(p, vs); gl.attachShader(p, fs); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) return null;
    gl.useProgram(p);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(p, 'a');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    var u = {};
    ['uRes', 'uDpr', 'uTime', 'uPill', 'uBlob', 'uSy', 'uRip', 'uMouse', 'uDark', 'uAmb', 'uK'].forEach(function (n) { u[n] = gl.getUniformLocation(p, n); });
    return { gl: gl, u: u };
  }


  /* ---------- вкладки ---------- */
  function tabs(nav) {
    var dark = 0;
    var glass = document.createElement('span'); glass.className = 'nimbus-glass'; glass.setAttribute('aria-hidden', 'true');
    var canvas = document.createElement('canvas'); canvas.className = 'nimbus-water'; canvas.setAttribute('aria-hidden', 'true');
    var dropEl = document.createElement('span'); dropEl.className = 'nimbus-drop'; dropEl.setAttribute('aria-hidden', 'true');
    nav.insertBefore(dropEl, nav.firstChild); nav.insertBefore(canvas, nav.firstChild); nav.insertBefore(glass, nav.firstChild);
    var s = {
      a: { x: 0, v: 0 }, b: { x: 0, v: 0 }, sy: { s: 1, v: 0 }, lead: 0, lagFrom: 0, sats: [], rips: [],
      t: { x: 0, cy: 0, q: 0, r: 21 }, q: 0, mouse: { x: 0, y: 0, s: 0, on: false },
      moving: false, split: false, ready: false, w: 0, h: 0, visible: true,
      reduce: !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches),
      raf: 0, last: 0, time: 0, glc: null
    };
    s.glc = initGL(canvas);
    nav.setAttribute('data-gl', s.glc ? 'on' : 'off');
    canvas.addEventListener('webglcontextlost', function (e) { e.preventDefault(); s.glc = null; nav.setAttribute('data-gl', 'off'); });
    function draw() {
      var T = s.t, sep = Math.abs(s.a.x - s.b.x);
      var thin = Math.min(sep / (2 * (s.q + T.r) || 1), 1), r = T.r * (1 - 0.3 * thin);
      if (s.glc) {
        var g = s.glc.gl, u = s.glc.u, dpr = Math.min(window.devicePixelRatio || 1, 2);
        g.viewport(0, 0, g.drawingBufferWidth, g.drawingBufferHeight);
        g.uniform2f(u.uRes, s.w + PAD * 2, s.h + PAD * 2);
        g.uniform1f(u.uDpr, dpr);
        g.uniform1f(u.uTime, s.time);
        g.uniform4f(u.uPill, PAD, PAD, s.w, s.h);
        var blobs = new Float32Array(24), sy = new Float32Array(6).fill(1);
        var head = s.lead === 0 ? s.a : s.b, tail = s.lead === 0 ? s.b : s.a;
        blobs.set([head.x, T.cy, s.q * (1 - 0.25 * thin), T.r * (1 - 0.12 * thin), tail.x, T.cy, s.q * (1 - 0.8 * thin), T.r * (1 - 0.5 * thin)], 0);
        sy[0] = sy[1] = s.sy.s;
        s.sats.slice(0, 4).forEach(function (d, i) { blobs.set([d.x, d.y, 0, d.r], 8 + i * 4); });
        g.uniform4fv(u.uBlob, blobs);
        g.uniform1fv(u.uSy, sy);
        var rips = new Float32Array(24);
        s.rips.slice(-6).forEach(function (p, i) { rips.set([p.x, p.y, p.age, p.amp], i * 4); });
        g.uniform4fv(u.uRip, rips);
        g.uniform3f(u.uMouse, s.mouse.x, s.mouse.y, s.mouse.s);
        g.uniform1f(u.uDark, dark);
        g.uniform1f(u.uAmb, s.reduce ? 0 : 1);
        g.uniform1f(u.uK, 13);
        g.clearColor(0, 0, 0, 0);
        g.clear(g.COLOR_BUFFER_BIT);
        g.drawArrays(g.TRIANGLES, 0, 3);
      } else {
        var left = Math.min(s.a.x, s.b.x) - s.q - r - PAD, width = sep + 2 * (s.q + r);
        dropEl.style.width = width + 'px';
        dropEl.style.transform = 'translateX(' + left + 'px) scaleY(' + s.sy.s + ')';
      }
    }

    function step(dt, now) {
      var T = s.t;
      s.time += dt;
      s.q += (T.q - s.q) * Math.min(1, dt * 12);
      var bodies = [s.a, s.b], still = true;
      if (s.reduce) {
        s.a.x = s.b.x = T.x; s.a.v = s.b.v = 0; s.sy.s = 1; s.sats = [];
      } else {
        bodies.forEach(function (B, i) {
          var lead = i === s.lead, k = lead ? 210 : 72, c = lead ? 25 : 13, f = !lead && now < s.lagFrom ? 0.08 : 1;
          B.v += (k * (T.x - B.x) - c * B.v) * dt * f;
          B.x += B.v * dt;
          var lo = PAD + 6 + s.q + T.r - 3, hi = PAD + s.w - 6 - s.q - T.r + 3;
          if (B.x < lo || B.x > hi) {
            s.sy.v -= Math.min(Math.abs(B.v), 900) * 0.0016;
            B.x = Math.max(lo, Math.min(hi, B.x));
            B.v *= -0.25;
          }
          if (Math.abs(T.x - B.x) > 0.3 || Math.abs(B.v) > 3) still = false;
        });
        var L = bodies[s.lead], syT = 1 - 0.2 * Math.min(Math.abs(L.v) / 1500, 1);
        s.sy.v += (280 * (syT - s.sy.s) - 9 * s.sy.v) * dt;
        s.sy.s += s.sy.v * dt;
        if (Math.abs(s.sy.s - 1) > 0.002 || Math.abs(s.sy.v) > 0.02) still = false;
        var sep = Math.abs(s.a.x - s.b.x);
        var r = T.r * (1 - 0.3 * Math.min(sep / (2 * (T.q + T.r) || 1), 1));
        if (!s.split && s.moving && sep - 2 * T.q - 2 * r > -6) {
          s.split = true;
          var lo2 = bodies[1 - s.lead], hi2 = bodies[s.lead];
          [0.3, 0.5, 0.72].forEach(function (t, j) {
            s.sats.push({
              x: lo2.x + (hi2.x - lo2.x) * t + (Math.random() - 0.5) * 6, y: T.cy + (Math.random() - 0.5) * 10,
              vx: (lo2.v + hi2.v) * 0.45, vy: (Math.random() - 0.5) * 60, r: 5 + Math.random() * 3 - j * 0.5, k: 70 + Math.random() * 90
            });
          });
        }
        s.sats = s.sats.filter(function (d) {
          d.vx += (d.k * (T.x - d.x) - 9 * d.vx) * dt;
          d.vy += (90 * (T.cy - d.y) - 7 * d.vy) * dt;
          d.x += d.vx * dt; d.y += d.vy * dt;
          d.x = Math.max(PAD + 8 + d.r, Math.min(PAD + s.w - 8 - d.r, d.x));
          d.y = Math.max(T.cy - T.r + d.r, Math.min(T.cy + T.r - d.r, d.y));
          if (Math.abs(T.x - d.x) < T.q + T.r * 0.5) d.r -= dt * 26;
          return d.r > 0.6;
        });
        if (s.sats.length) still = false;
      }
      if (s.moving && still) {
        s.moving = false;
        if (s.split || !s.reduce) s.rips.push({ x: T.x, y: T.cy, age: 0, amp: s.split ? 1.2 : 0.7 });
      }
      s.rips.forEach(function (p) { p.age += dt; });
      s.rips = s.rips.filter(function (p) { return p.age < 2.4; });
      s.mouse.s += ((s.mouse.on ? 1 : 0) - s.mouse.s) * Math.min(1, dt * 7);
    }

    function loop() {
      if (s.raf) return;
      s.last = performance.now();
      var tick = function (now) {
        var dt = Math.min((now - s.last) / 1000, 1 / 30);
        s.last = now;
        step(dt, now); draw();
        var busy = s.moving || s.rips.length || s.sats.length || s.mouse.s > 0.01;
        if (s.visible && (!s.reduce || busy)) s.raf = requestAnimationFrame(tick); else s.raf = 0;
      };
      s.raf = requestAnimationFrame(tick);
    }


    var items = function () { return Array.prototype.filter.call(nav.querySelectorAll('[role=tab]'), function (t) { return !t.hidden; }); };
    function measure() {
      var list = items(), el = null;
      list.forEach(function (t) { if (t.getAttribute('aria-selected') === 'true') el = t; });
      if (!el || !nav.offsetWidth) return;
      s.w = nav.offsetWidth; s.h = nav.offsetHeight;
      var T = s.t;
      T.r = el.offsetHeight / 2;
      T.q = Math.max(0, el.offsetWidth / 2 - T.r);
      T.cy = PAD + el.offsetTop + el.offsetHeight / 2;
      var nx = PAD + el.offsetLeft + el.offsetWidth / 2;
      if (!s.ready) { s.a.x = s.b.x = nx; s.q = T.q; s.ready = true; }
      else if (Math.abs(nx - T.x) > 0.5) {
        s.lead = Math.abs(nx - s.a.x) <= Math.abs(nx - s.b.x) ? 0 : 1;
        s.lagFrom = performance.now() + 70; s.moving = true; s.split = false;
        if (el.scrollIntoView) el.scrollIntoView({ inline: 'center', block: 'nearest', behavior: s.reduce ? 'auto' : 'smooth' });
      }
      T.x = nx;
      var dpr = Math.min(window.devicePixelRatio || 1, 2), W = s.w + PAD * 2, Hh = s.h + PAD * 2;
      if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(Hh * dpr)) {
        canvas.width = Math.round(W * dpr); canvas.height = Math.round(Hh * dpr);
        canvas.style.width = W + 'px'; canvas.style.height = Hh + 'px';
      }
      loop();
    }
    function local(cx, cy) { var r = nav.getBoundingClientRect(); return { x: cx - r.left + PAD, y: cy - r.top + PAD }; }
    nav.addEventListener('pointermove', function (e) {
      if (e.pointerType !== 'mouse') return;
      var p = local(e.clientX, e.clientY); s.mouse.x = p.x; s.mouse.y = p.y; s.mouse.on = true; loop();
    });
    nav.addEventListener('pointerleave', function () { s.mouse.on = false; loop(); });
    nav.addEventListener('pointerdown', function (e) {
      if (s.reduce) return;
      var p = local(e.clientX, e.clientY); s.rips.push({ x: p.x, y: p.y, age: 0, amp: 0.8 }); loop();
    });
    if ('IntersectionObserver' in window) new IntersectionObserver(function (es) { s.visible = es[0].isIntersecting; if (s.visible) loop(); }).observe(nav);
    document.addEventListener('visibilitychange', function () { s.visible = document.visibilityState === 'visible'; if (s.visible) loop(); });
    // Приложение само переключает вкладки и прячет/показывает их: вода следит за aria-selected и hidden.
    if ('MutationObserver' in window) new MutationObserver(function () { s.ready = s.ready && nav.offsetWidth === s.w; measure(); })
      .observe(nav, { subtree: true, attributes: true, attributeFilter: ['aria-selected', 'hidden'] });
    var lastW = -1;
    new ResizeObserver(function () { var w = nav.offsetWidth; if (w !== lastW) { lastW = w; s.ready = false; measure(); } }).observe(nav);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { s.ready = false; measure(); });
    measure();
    return { refresh: function () { s.ready = false; measure(); } };
  }

  /* ---------- календарь ---------- */
  function cal(panel) {
    var glass = document.createElement('span'); glass.className = 'nimbus-glass'; glass.setAttribute('aria-hidden', 'true');
    var canvas = document.createElement('canvas'); canvas.className = 'nimbus-water'; canvas.setAttribute('aria-hidden', 'true');
    panel.insertBefore(canvas, panel.firstChild); panel.insertBefore(glass, panel.firstChild);

    var s = {
      w: 0, h: 0, rows: [], rips: [], mouse: { x: 0, y: 0, s: 0, on: false },
      raf: 0, last: 0, time: 0, visible: true, glc: initGL(canvas), moving: false, settledAt: null,
      reduce: !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)
    };
    panel.setAttribute('data-gl', s.glc ? 'on' : 'off');
    for (var i = 0; i < ROWS; i++) s.rows.push({ l: { x: 0, v: 0 }, r: { x: 0, v: 0 }, y: 0, rad: { x: 0, v: 0 }, t: { l: 0, r: 0, rad: 0 }, lead: 'r' });
    canvas.addEventListener('webglcontextlost', function (e) { e.preventDefault(); s.glc = null; panel.setAttribute('data-gl', 'off'); });

    function draw() {
      if (!s.glc) return;
      var g = s.glc.gl, u = s.glc.u, dpr = Math.min(window.devicePixelRatio || 1, 2);
      g.viewport(0, 0, g.drawingBufferWidth, g.drawingBufferHeight);
      g.uniform2f(u.uRes, s.w + PAD * 2, s.h + PAD * 2);
      g.uniform1f(u.uDpr, dpr);
      g.uniform1f(u.uTime, s.time);
      g.uniform4f(u.uPill, PAD, PAD, s.w, s.h);
      var blobs = new Float32Array(24), sy = new Float32Array(6).fill(1);
      s.rows.forEach(function (row, i) {
        var R = Math.min(row.rad.x, Math.abs(row.r.x - row.l.x) / 2);
        if (R < 0.7) return;
        blobs.set([(row.l.x + row.r.x) / 2, row.y, Math.max(0, Math.abs(row.r.x - row.l.x) / 2 - R), R], i * 4);
      });
      g.uniform4fv(u.uBlob, blobs);
      g.uniform1fv(u.uSy, sy);
      var rips = new Float32Array(24);
      s.rips.slice(-6).forEach(function (p, i) { rips.set([p.x, p.y, p.age, p.amp], i * 4); });
      g.uniform4fv(u.uRip, rips);
      g.uniform3f(u.uMouse, s.mouse.x, s.mouse.y, s.mouse.s);
      g.uniform1f(u.uDark, 0);
      g.uniform1f(u.uAmb, 0);       // без постоянной ряби: пока ничего не двигается, кадры не рисуются
      g.uniform1f(u.uK, 7);
      g.clearColor(0, 0, 0, 0);
      g.clear(g.COLOR_BUFFER_BIT);
      g.drawArrays(g.TRIANGLES, 0, 3);
    }

    function spring(B, target, k, c, dt) {
      B.v += (k * (target - B.x) - c * B.v) * dt;
      B.x += B.v * dt;
      return Math.abs(target - B.x) > 0.3 || Math.abs(B.v) > 3;
    }
    function step(dt) {
      s.time += dt;
      var still = true;
      s.rows.forEach(function (row) {
        if (s.reduce) { row.l.x = row.t.l; row.r.x = row.t.r; row.rad.x = row.t.rad; row.l.v = row.r.v = row.rad.v = 0; return; }
        var lk = row.lead === 'l' ? [210, 25] : [72, 13], rk = row.lead === 'r' ? [210, 25] : [72, 13];
        if (spring(row.l, row.t.l, lk[0], lk[1], dt)) still = false;
        if (spring(row.r, row.t.r, rk[0], rk[1], dt)) still = false;
        if (spring(row.rad, row.t.rad, 180, 22, dt)) still = false;
        if (row.rad.x < 0) row.rad.x = 0;
      });
      if (s.moving && still) {
        s.moving = false;
        if (s.settledAt && !s.reduce) s.rips.push({ x: s.settledAt.x, y: s.settledAt.y, age: 0, amp: 0.8 });
      }
      s.moving = s.moving || !still;
      s.rips.forEach(function (p) { p.age += dt; });
      s.rips = s.rips.filter(function (p) { return p.age < 2.4; });
      s.mouse.s += ((s.mouse.on ? 1 : 0) - s.mouse.s) * Math.min(1, dt * 7);
    }
    function loop() {
      if (s.raf) return;
      s.last = performance.now();
      var tick = function (now) {
        var dt = Math.min((now - s.last) / 1000, 1 / 30);
        s.last = now; step(dt); draw();
        var busy = s.moving || s.rips.length || s.mouse.s > 0.01;
        if (s.visible && busy) s.raf = requestAnimationFrame(tick); else s.raf = 0;
      };
      s.raf = requestAnimationFrame(tick);
    }
    function measure() {
      s.w = panel.offsetWidth; s.h = panel.offsetHeight;
      var dpr = Math.min(window.devicePixelRatio || 1, 2), W = s.w + PAD * 2, Hh = s.h + PAD * 2;
      if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(Hh * dpr)) {
        canvas.width = Math.round(W * dpr); canvas.height = Math.round(Hh * dpr);
        canvas.style.width = W + 'px'; canvas.style.height = Hh + 'px';
      }
    }

    function set(segs) {
      measure();
      var seen = {};
      segs.forEach(function (g) {
        seen[g.row] = true;
        var row = s.rows[g.row], x0 = g.x0 + PAD, x1 = g.x1 + PAD;
        if (!(row.rad.x > 0.7) || row.t.rad === 0) {          // капля в этой строке только появляется: растёт из середины
          row.l.x = row.r.x = (x0 + x1) / 2; row.l.v = row.r.v = 0; row.rad.x = 0; row.rad.v = 0;
        }
        row.lead = Math.abs(x0 - row.l.x) > Math.abs(x1 - row.r.x) ? 'l' : 'r';
        row.t.l = x0; row.t.r = x1; row.t.rad = g.rad; row.y = g.y + PAD;
        s.settledAt = { x: (x0 + x1) / 2, y: g.y + PAD };
      });
      s.rows.forEach(function (row, i) { if (!seen[i]) row.t.rad = 0; });   // строки вне периода: капля втягивается
      s.moving = true; loop();
    }

    panel.addEventListener('pointermove', function (e) {
      if (e.pointerType !== 'mouse') return;
      var r = panel.getBoundingClientRect(); s.mouse.x = e.clientX - r.left + PAD; s.mouse.y = e.clientY - r.top + PAD; s.mouse.on = true; loop();
    });
    panel.addEventListener('pointerleave', function () { s.mouse.on = false; loop(); });
    panel.addEventListener('pointerdown', function (e) {
      if (s.reduce) return;
      var r = panel.getBoundingClientRect(); s.rips.push({ x: e.clientX - r.left + PAD, y: e.clientY - r.top + PAD, age: 0, amp: 0.6 }); loop();
    });
    if ('IntersectionObserver' in window) new IntersectionObserver(function (es) { s.visible = es[0].isIntersecting; if (s.visible) loop(); }).observe(panel);
    return { set: set, measure: measure, get ok() { return !!s.glc; } };
  }

  window.LiquidWater = { tabs: tabs, cal: cal };
})();
