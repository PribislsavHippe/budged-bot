// Pastel Ribbon Field: parameters and CSS stops supplied by the owner from 21st.dev.
(() => {
  const canvas=document.getElementById('gradient-canvas');
  if(!canvas)return;
  const gl=canvas.getContext('webgl',{alpha:false,antialias:false,depth:false,stencil:false});
  if(!gl){canvas.hidden=true;return;} // The supplied CSS approximation remains available.
  try {
  const vertex='attribute vec2 position;varying vec2 uv;void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}';
  const fragment=`precision highp float;
    varying vec2 uv;uniform vec2 size;uniform float phase;
    void main(){
      float angle=radians(90.0+sin(phase*0.6)*28.0);
      vec2 axis=vec2(sin(angle),-cos(angle));
      vec2 across=vec2(cos(angle),sin(angle));
      vec2 p=(vec2(uv.x,1.0-uv.y)-0.5)*size;
      float along=dot(p,axis)/dot(abs(axis),size)+0.5;
      float crossAxis=dot(p,across)/dot(abs(across),size)+0.5;
      along+=(12.0/100.0)*0.35*sin(crossAxis*2.4*6.28318530718+20.75+phase*1.2);
      vec3 bubblegum=vec3(239.,193.,218.)/255.;
      vec3 matcha=vec3(206.,201.,159.)/255.;
      vec3 mauve=vec3(220.,214.,246.)/255.;
      vec3 blue=vec3(181.,208.,217.)/255.;
      // Feather intervals from the supplied CSS export (softness 26, scale 68).
      vec3 color=mix(bubblegum,matcha,clamp((along-.2)/.039,0.,1.));
      color=mix(color,mauve,clamp((along-.511)/.078,0.,1.));
      color=mix(color,blue,clamp((along-.811)/.039,0.,1.));
      gl_FragColor=vec4(color,1.);
    }`;
  function compile(type,source){const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(shader));return shader;}
  const program=gl.createProgram();gl.attachShader(program,compile(gl.VERTEX_SHADER,vertex));gl.attachShader(program,compile(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
  const position=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
  const size=gl.getUniformLocation(program,'size'),phase=gl.getUniformLocation(program,'phase');
  function resize(){const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.ceil(innerWidth*dpr);canvas.height=Math.ceil(innerHeight*dpr);if(gl.isContextLost())return;gl.viewport(0,0,canvas.width,canvas.height);gl.uniform2f(size,innerWidth,innerHeight);draw(elapsed);}
  function draw(t){gl.uniform1f(phase,t);gl.drawArrays(gl.TRIANGLES,0,6);}
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');let frame=null,previous=null,elapsed=0;
  function tick(now){frame=null;if(previous!==null)elapsed+=(now-previous)/1000;previous=now;draw(elapsed);frame=requestAnimationFrame(tick);}
  function restart(){if(frame!==null)cancelAnimationFrame(frame);frame=null;previous=null;if(document.hidden||gl.isContextLost())return;if(reduced.matches)draw(0);else frame=requestAnimationFrame(tick);}
  window.addEventListener('resize',resize);document.addEventListener('visibilitychange',restart);reduced.addEventListener('change',restart);
  canvas.addEventListener('webglcontextlost',()=>{if(frame!==null)cancelAnimationFrame(frame);frame=null;canvas.hidden=true;});
  resize();restart();
  } catch (_) {canvas.hidden=true;} // Rendering never blocks the journal.
})();
