// Фон: цвета владельца (Pastel Ribbon Field с 21st.dev), разложенные пятнами и смешанные в OKLCH.
(() => {
  const canvas=document.getElementById('gradient-canvas');
  if(!canvas)return;
  const gl=canvas.getContext('webgl',{alpha:false,antialias:false,depth:false,stencil:false});
  if(!gl){canvas.hidden=true;return;} // The supplied CSS approximation remains available.
  try {
  const vertex='attribute vec2 position;varying vec2 uv;void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}';
  // Пятна вместо полос (mesh), цвета смешиваются в OKLCH — без серой середины между розовым и матчей.
  // При открытии цвет спокойно проявляется из почти белого, пятна расходятся из одной точки (~3 с).
  const fragment=`precision highp float;
    varying vec2 uv;uniform vec2 size;uniform float phase;uniform float intro;
    float E;vec2 K;
    vec3 oklab(vec3 c){c=pow(c,vec3(2.2));
      float l=pow(.4122214708*c.r+.5363325363*c.g+.0514459929*c.b,1./3.);
      float m=pow(.2119034982*c.r+.6806995451*c.g+.1073969566*c.b,1./3.);
      float s=pow(.0883024619*c.r+.2817188376*c.g+.6299787005*c.b,1./3.);
      return vec3(.2104542553*l+.7936177850*m-.0040720468*s,1.9779984951*l-2.4285922050*m+.4505937099*s,.0259040371*l+.7827717662*m-.8086757660*s);}
    vec3 rgb(vec3 o){float l=o.x+.3963377774*o.y+.2158037573*o.z,m=o.x-.1055613458*o.y-.0638541728*o.z,s=o.x-.0894841775*o.y-1.2914855480*o.z;
      l=l*l*l;m=m*m*m;s=s*s*s;
      return pow(max(vec3(4.0767416621*l-3.3077115913*m+.2309699292*s,-1.2684380046*l+2.6097574011*m-.3413193965*s,-.0041960863*l-.7034186147*m+1.7076147010*s),0.),vec3(1./2.2));}
    float blob(vec2 p,vec2 c,float r){c=mix(vec2(.5,.22),c,E)*K;r*=mix(.55,1.,E);vec2 d=p-c;return exp(-dot(d,d)/(r*r));}
    void main(){
      E=intro*intro*(3.-2.*intro);E=E*E*(3.-2.*E);
      float t=phase*.35;
      vec2 q=vec2(uv.x,1.-uv.y);
      q+=.035*vec2(sin(q.y*3.1+t*1.7),cos(q.x*2.7-t*1.3));
      K=size/max(size.x,size.y);vec2 p=q*K;float r=.24;
      // Голубой угол сверху справа, сиреневый справа, матча слева и в середине, розовый снизу слева.
      float w0=blob(p,vec2(.08+.06*sin(t*.9),.92+.05*cos(t*.7)),r);
      float w1=blob(p,vec2(.20+.08*cos(t*.8+1.),.55+.10*sin(t*1.1)),r*1.15);
      float w2=blob(p,vec2(.45+.10*sin(t*.7+4.),.08+.05*cos(t*.6)),r*.9);
      float w3=blob(p,vec2(.92+.05*sin(t*.6+2.),.50+.12*cos(t*.9+1.)),r*1.1);
      float w4=blob(p,vec2(.55+.08*cos(t*.5),.30+.06*sin(t*.8+2.)),r*.9);
      float w5=blob(p,vec2(.95+.04*cos(t+3.),.05+.04*sin(t*.8)),r*.85);
      float w6=blob(p,vec2(.70+.06*sin(t*.7+1.),.85+.05*cos(t*.9)),r);
      vec3 pink=oklab(vec3(239.,193.,218.)/255.),matcha=oklab(vec3(206.,201.,159.)/255.),
           mauve=oklab(vec3(220.,214.,246.)/255.),blue=oklab(vec3(181.,208.,217.)/255.);
      float W=w0+w1+w2+w3+w4+w5+w6+1e-6;float wm=w1+w2+w6,wv=w3+w4;
      float L=(w0*pink.x+wm*matcha.x+wv*mauve.x+w5*blue.x)/W;
      float C=(w0*length(pink.yz)+wm*length(matcha.yz)+wv*length(mauve.yz)+w5*length(blue.yz))/W;
      vec2 h=w0*normalize(pink.yz)+wm*normalize(matcha.yz)+wv*normalize(mauve.yz)+w5*normalize(blue.yz);
      vec3 c=mix(vec3(.975,0.,0.),vec3(L,C*normalize(h+1e-6)),E);
      gl_FragColor=vec4(rgb(c),1.);
    }`;
  function compile(type,source){const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(shader));return shader;}
  const program=gl.createProgram();gl.attachShader(program,compile(gl.VERTEX_SHADER,vertex));gl.attachShader(program,compile(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
  const position=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(position);gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
  const size=gl.getUniformLocation(program,'size'),phase=gl.getUniformLocation(program,'phase'),intro=gl.getUniformLocation(program,'intro');
  function resize(){const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.ceil(innerWidth*dpr);canvas.height=Math.ceil(innerHeight*dpr);if(gl.isContextLost())return;gl.viewport(0,0,canvas.width,canvas.height);gl.uniform2f(size,innerWidth,innerHeight);draw(elapsed);}
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),openedAt=performance.now();
  function draw(t){gl.uniform1f(phase,t);gl.uniform1f(intro,reduced.matches?1:Math.min(1,(performance.now()-openedAt)/3200));gl.drawArrays(gl.TRIANGLES,0,6);}
  let frame=null,previous=null,elapsed=0;
  function tick(now){frame=null;if(previous!==null)elapsed+=(now-previous)/1000;previous=now;draw(elapsed);frame=requestAnimationFrame(tick);}
  function restart(){if(frame!==null)cancelAnimationFrame(frame);frame=null;previous=null;if(document.hidden||gl.isContextLost())return;if(reduced.matches)draw(0);else frame=requestAnimationFrame(tick);}
  window.addEventListener('resize',resize);document.addEventListener('visibilitychange',restart);reduced.addEventListener('change',restart);
  canvas.addEventListener('webglcontextlost',()=>{if(frame!==null)cancelAnimationFrame(frame);frame=null;canvas.hidden=true;});
  resize();restart();
  } catch (_) {canvas.hidden=true;} // Rendering never blocks the journal.
})();
