

// RL_IK_WORKSPACE_JOINT_CONTROL_V6

import * as THREE from "./three.module.js";

import {OrbitControls}
    from "./controls/OrbitControls.js";

import {TransformControls}
    from "./controls/TransformControls.js";

const get = id => document.getElementById(id);

function main(){

    const ikPanel = get("panel-ik");

    if(!ikPanel){
        throw Error("IK panel not found");
    }

    // Restore original SIDE / TOP views.

    const original =
        get("side")?.closest(".views");

    if(original){

        original.hidden = false;
        original.style.display = "";
    }

    // Reuse existing CAD panel.
    // CAD rendering is no longer used.

    const host =
        get("rl-cad-panel") ||
        document.createElement("div");

    host.id = "rl-cad-panel";

    host.style.cssText =
        "background:#091d30;padding:14px;"+
        "border:1px solid #355570;"+
        "border-radius:14px;margin:16px 0;"+
        "width:100%;box-sizing:border-box";

    // Locate old XYZ slider / Jog TCP card.

    const heading = [...ikPanel.querySelectorAll(
        "h2,h3,h4"
    )].find(e=>
        e.textContent.trim()==="Jog TCP"
    );

    const oldJog =
        heading?.closest(".card") ||
        ikPanel.querySelector("#jog-step")
            ?.closest(".card");

    // Keep old elements in DOM so existing
    // Dashboard functions remain compatible.

    if(
        oldJog &&
        oldJog!==ikPanel &&
        ikPanel.contains(oldJog) &&
        !oldJog.querySelector("#ik-preview") &&
        !oldJog.querySelector("#ik-x")
    ){

        oldJog.before(host);
        oldJog.style.display="none";

    }else{

        const actions =
            get("ik-preview")?.closest(".actions");

        if(actions){

            actions.before(host);

        }else{

            ikPanel.appendChild(host);
        }

        console.warn("Old jog card not matched");
    }

    host.innerHTML = `

        <h2>Interactive 3D Workspace</h2>

        <div id="rl-joint-tools"
             style="display:flex;gap:6px;
                    flex-wrap:wrap;margin:12px 0">

            <button data-control="tcp">TCP XYZ</button>
            <button data-control="1">J1</button>
            <button data-control="2">J2</button>
            <button data-control="3">J3</button>
            <button data-control="4">J4</button>

        </div>

        <div style="display:flex;gap:6px;
                    flex-wrap:wrap;margin:12px 0">

            <button id="rl-view-3d">3D</button>
            <button id="rl-view-side">SIDE</button>
            <button id="rl-view-top">TOP</button>
            <button id="rl-view-reset">RESET</button>

        </div>

        <label style="display:flex;gap:8px;
                      align-items:center;
                      margin:12px 0">

            <input type="checkbox"
                   id="rl-workspace-real"
                   style="width:auto">

            Move REAL robot on release

        </label>

        <div id="rl-workspace-stage"
             style="width:100%;height:480px;
                    overflow:hidden;
                    border:1px solid #355570;
                    border-radius:12px;
                    background:#081b2d">
        </div>

        <div id="rl-workspace-q"
             style="line-height:1.9;
                    margin:14px 0;color:#76e7c4">
        </div>

        <div id="rl-workspace-pos"
             style="line-height:1.9;color:#ffd19a">
        </div>

        <p id="rl-workspace-status">
            Waiting for feedback...
        </p>

        <small>
            Drag XYZ arrows or select J1-J4
            and drag its rotation ring.
            Release to execute when enabled.
            No collision detection.
        </small>

    `;

    host.querySelectorAll("button").forEach(
        b=>b.type="button"
    );

    const stage = get("rl-workspace-stage");
    const realEnable = get("rl-workspace-real");

    function status(text,bad=false){

        const el = get("rl-workspace-status");

        el.textContent=text;

        el.style.color =
            bad?"#ff8585":"#76e7c4";
    }

    // ==================================
    // 3D scene
    // ==================================

    const scene = new THREE.Scene();

    scene.background =
        new THREE.Color("#081b2d");

    const camera = new THREE.OrthographicCamera(
        -250,250,250,-250,0.1,10000
    );

    camera.up.set(0,0,1);

    camera.position.set(350,-450,350);

    const renderer = new THREE.WebGLRenderer({
        antialias:true
    });

    renderer.setPixelRatio(
        Math.min(window.devicePixelRatio,2)
    );

    renderer.domElement.style.cssText =
        "display:block;width:100%;height:100%;"+
        "touch-action:none";

    stage.appendChild(renderer.domElement);

    const orbit = new OrbitControls(
        camera,renderer.domElement
    );

    orbit.enableDamping=true;
    orbit.minZoom=0.5;
    orbit.maxZoom=5;

    scene.add(
        new THREE.HemisphereLight(
            0xffffff,0x536a80,2
        )
    );

    const light = new THREE.DirectionalLight(
        0xffffff,2.5
    );

    light.position.set(300,-400,500);

    scene.add(light);

    const grid = new THREE.GridHelper(
        500,20,0x65849b,0x29465d
    );

    grid.rotation.x=Math.PI/2;

    scene.add(grid);

    scene.add(new THREE.AxesHelper(65));

    const robot = new THREE.Group();

    scene.add(robot);

    const mat = color =>
        new THREE.MeshStandardMaterial({
            color,
            roughness:0.5,
            metalness:0.2
        });

    const gray=mat(0x8198ac);
    const green=mat(0x76e7c4);
    const orange=mat(0xffcd94);
    const white=mat(0xddeeff);

    const base = new THREE.Mesh(
        new THREE.CylinderGeometry(
            32,32,10,48
        ),
        gray
    );

    base.rotation.x=Math.PI/2;
    base.position.z=-5;

    robot.add(base);

    const links=[];
    const joints=[];

    for(let i=0;i<4;i++){

        const link=new THREE.Mesh(

            new THREE.CylinderGeometry(
                i===3?10:7,
                i===3?10:7,
                1,24
            ),

            [gray,green,green,orange][i]
        );

        robot.add(link);
        links.push(link);
    }

    for(let i=0;i<5;i++){

        const joint=new THREE.Mesh(
            new THREE.SphereGeometry(
                i===4?6:9,24,16
            ),
            i===4?orange:white
        );

        robot.add(joint);
        joints.push(joint);
    }

    // ==================================
    // Existing FK geometry
    // ==================================

    const D = Math.PI/180;

    function points(q){

        const [a,b,c,d]=q.map(v=>v*D);

        const angles=[b,b+c,b+c+d];

        const lengths=[
            Number(geom.l1),
            Number(geom.l2),
            Number(geom.l3)
        ];

        let r=0;
        let z=Number(geom.h);

        const pts=[
            new THREE.Vector3(0,0,0),
            new THREE.Vector3(0,0,z)
        ];

        for(let i=0;i<3;i++){

            r+=lengths[i]*Math.cos(angles[i]);

            z+=lengths[i]*Math.sin(angles[i]);

            pts.push(
                new THREE.Vector3(
                    r*Math.cos(a),
                    r*Math.sin(a),
                    z
                )
            );
        }

        return pts;
    }

    function drawRobot(q){

        const pts=points(q);

        pts.forEach((p,i)=>{
            joints[i].position.copy(p);
        });

        for(let i=0;i<4;i++){

            const direction=
                pts[i+1].clone().sub(pts[i]);

            const len=direction.length();

            links[i].visible=len>0.001;

            if(len<=0.001) continue;

            links[i].position.copy(pts[i])
                .addScaledVector(direction,0.5);

            links[i].scale.y=len;

            links[i].quaternion.setFromUnitVectors(
                new THREE.Vector3(0,1,0),
                direction.normalize()
            );
        }

        return pts;
    }

    // ==================================
    // 3D theoretical workspace
    // ==================================

    const envelope = new THREE.Group();

    scene.add(envelope);

    let geometryKey="";
    let span=250;

    const center =
        new THREE.Vector3(0,0,110);

    function resize(){

        const w=stage.clientWidth;
        const h=stage.clientHeight;

        if(w<2 || h<2) return;

        const a=w/h;

        renderer.setSize(w,h,false);

        camera.left=-span*Math.max(1,a);
        camera.right=span*Math.max(1,a);

        camera.top=span*Math.max(1,1/a);
        camera.bottom=-span*Math.max(1,1/a);

        camera.updateProjectionMatrix();
    }

    function view(type="3d"){

        const dir={

            "3d":[1.3,-1.7,1.1],
            side:[0,-2,0],
            top:[0,-0.001,2]

        }[type];

        camera.position.copy(center).add(
            new THREE.Vector3(...dir)
                .multiplyScalar(span*2)
        );

        orbit.target.copy(center);

        camera.zoom=1;

        camera.lookAt(center);
        camera.updateProjectionMatrix();

        orbit.update();
        resize();
    }

    function updateEnvelope(){

        const h=Number(geom.h);

        const l=[
            Number(geom.l1),
            Number(geom.l2),
            Number(geom.l3)
        ];

        const key=JSON.stringify([h,...l]);

        if(key===geometryKey) return;

        if(
            !Number.isFinite(h) ||
            !l.every(v=>Number.isFinite(v)&&v>0)
        ){
            throw Error("Invalid geometry");
        }

        geometryKey=key;

        for(const obj of [...envelope.children]){

            envelope.remove(obj);

            obj.geometry.dispose();
            obj.material.dispose();
        }

        const reach=l.reduce((a,b)=>a+b,0);

        function dashed(pts){

            const line=new THREE.Line(

                new THREE.BufferGeometry()
                    .setFromPoints(pts),

                new THREE.LineDashedMaterial({
                    color:0x39c7a6,
                    dashSize:7,
                    gapSize:5,
                    transparent:true,
                    opacity:0.35,
                    depthWrite:false
                })
            );

            line.computeLineDistances();

            envelope.add(line);
        }

        for(let i=0;i<12;i++){

            const a=i*2*Math.PI/12;
            const pts=[];

            for(let k=0;k<=50;k++){

                const b=k*Math.PI/100;

                pts.push(
                    new THREE.Vector3(
                        reach*Math.cos(b)*Math.cos(a),
                        reach*Math.cos(b)*Math.sin(a),
                        h+reach*Math.sin(b)
                    )
                );
            }

            dashed(pts);
        }

        for(let i=0;i<5;i++){

            const b=i*Math.PI/10;
            const r=reach*Math.cos(b);
            const z=h+reach*Math.sin(b);

            const pts=[];

            for(let k=0;k<=100;k++){

                const a=k*2*Math.PI/100;

                pts.push(
                    new THREE.Vector3(
                        r*Math.cos(a),
                        r*Math.sin(a),
                        z
                    )
                );
            }

            dashed(pts);
        }

        span=reach*1.12;

        center.set(0,0,h+reach*0.35);

        view("3d");
    }

    // ==================================
    // XYZ and Joint rotation gizmo
    // ==================================

    const handle=new THREE.Object3D();

    scene.add(handle);

    const gizmo=new TransformControls(
        camera,renderer.domElement
    );

    gizmo.attach(handle);
    gizmo.setSize(0.85);

    scene.add(gizmo);

    const ghost=new THREE.Line(

        new THREE.BufferGeometry(),

        new THREE.LineBasicMaterial({
            color:0xffd19a,
            transparent:true,
            opacity:0.7
        })
    );

    scene.add(ghost);

    ghost.visible=false;

    const marker=new THREE.Mesh(
        new THREE.SphereGeometry(5,20,16),
        new THREE.MeshBasicMaterial({
            color:0xffd19a
        })
    );

    scene.add(marker);

    marker.visible=false;

    let selected="tcp";
    let drag=null;
    let sending=false;
    let previousMode=mode;

    // Conservative initial movement per drag.

    const MAX_MM=20;
    const MAX_DEG=10;

    const active=()=>
        get("tab-ik")
            ?.classList.contains("active");

    function ready(){

        if(!active() || working) return false;

        const q=currentQ();

        if(
            !q ||
            q.length!==4 ||
            !q.every(Number.isFinite)
        ){
            return false;
        }

        if(mode==="real"){

            return Boolean(
                geom.realCalibrated &&
                live.connected &&
                live.info_ready &&
                !live.busy &&
                !live.emotion_armed
            );

        }

        return mode==="sim";
    }

    function selectControl(type){

        if(sending || gizmo.dragging) return;

        selected=type;

        ghost.visible=false;
        marker.visible=false;

        host.querySelectorAll(
            "[data-control]"
        ).forEach(b=>{

            const on=b.dataset.control===type;

            b.style.background=
                on?"#76e7c4":"";

            b.style.color=
                on?"#092b28":"";
        });

        if(type==="tcp"){

            gizmo.setMode("translate");
            gizmo.setSpace("world");

            gizmo.showX=true;
            gizmo.showY=true;
            gizmo.showZ=true;

            status("Drag X, Y or Z arrow");

        }else{

            gizmo.setMode("rotate");
            gizmo.setSpace("local");

            gizmo.showX=false;
            gizmo.showY=false;
            gizmo.showZ=true;

            status(
                "Drag joint J"+type+
                " rotation ring"
            );
        }

        update();
    }

    host.querySelectorAll(
        "[data-control]"
    ).forEach(b=>{

        b.onclick=()=>
            selectControl(b.dataset.control);
    });

    function alignHandle(q,pts){

        if(selected==="tcp"){

            handle.position.copy(pts[4]);

            handle.quaternion.identity();

        }else{

            const i=Number(selected)-1;

            handle.position.copy(pts[i]);

            // J1 rotates about global Z.
            // J2-J4 pitch axes rotate with J1.

            const a=q[0]*D;

            const axis=i===0

                ? new THREE.Vector3(0,0,1)

                : new THREE.Vector3(
                    Math.sin(a),
                    -Math.cos(a),
                    0
                );

            handle.quaternion.setFromUnitVectors(
                new THREE.Vector3(0,0,1),
                axis
            );
        }
    }

    function update(){

        if(previousMode!==mode){

            previousMode=mode;
            realEnable.checked=false;

            drag=null;
        }

        if(!active()){

            realEnable.checked=false;
            gizmo.enabled=false;
            gizmo.visible=false;

            return;
        }

        updateEnvelope();

        const q=currentQ();

        const valid=Boolean(

            q &&
            q.length===4 &&
            q.every(Number.isFinite) &&

            (
                mode==="sim" ||
                (
                    geom.realCalibrated &&
                    live.connected &&
                    live.info_ready
                )
            )
        );

        robot.visible=valid;

        if(!valid){

            gizmo.visible=false;
            gizmo.enabled=false;

            get("rl-workspace-q").textContent=
                "Joint feedback unavailable";

            return;
        }

        const pts=drawRobot(q);
        const p=fk(q);

        // Verify that visualization agrees with FK.

        const error=pts[4].distanceTo(
            new THREE.Vector3(p.x,p.y,p.z)
        );

        if(error>0.1){

            gizmo.enabled=false;
            gizmo.visible=false;

            status("3D model / FK mismatch",true);

            return;
        }

        get("rl-workspace-q").textContent=
            q.map((v,i)=>
                "J"+(i+1)+": "+v.toFixed(2)+" deg"
            ).join(" | ");

        get("rl-workspace-pos").textContent=
            "TCP: X "+p.x.toFixed(2)+
            " | Y "+p.y.toFixed(2)+
            " | Z "+p.z.toFixed(2)+" mm";

        realEnable.disabled=
            mode!=="real" ||
            sending ||
            working ||
            !ready();

        if(gizmo.dragging) return;

        gizmo.enabled=ready()&&!sending;
        gizmo.visible=gizmo.enabled;

        alignHandle(q,pts);
    }

    // ==================================
    // Drag preview
    // ==================================

    gizmo.addEventListener(
        "dragging-changed",
        e=>orbit.enabled=!e.value
    );

    gizmo.addEventListener("mouseDown",()=>{

        if(!ready() || sending) return;

        const axis=String(gizmo.axis||"");

        if(
            selected==="tcp"
                ? !["X","Y","Z"].includes(axis)
                : axis!=="Z"
        ){
            drag=null;
            return;
        }

        const q=currentQ();

        drag={
            q:[...q],
            mode,
            selected,
            axis,
            position:handle.position.clone(),
            rotation:handle.quaternion.clone(),
            delta:0,
            execute:realEnable.checked
        };

        ghost.visible=false;
        marker.visible=false;
    });

    const limit=(v,m)=>
        Math.max(-m,Math.min(m,v));

    gizmo.addEventListener("objectChange",()=>{

        if(!drag) return;

        if(drag.selected==="tcp"){

            const a=drag.axis.toLowerCase();

            drag.delta=limit(
                handle.position[a]-drag.position[a],
                MAX_MM
            );

            const target=drag.position.clone();

            target[a]+=drag.delta;

            handle.position.copy(target);

            marker.position.copy(target);
            marker.visible=true;

            status(
                "TCP "+drag.axis+": "+
                drag.delta.toFixed(2)+" mm"
            );

        }else{

            const relative=drag.rotation.clone()
                .invert()
                .multiply(handle.quaternion);

            let angle=2*Math.atan2(
                relative.z,relative.w
            )/D;

            angle=((angle+180)%360+360)%360-180;

            drag.delta=limit(angle,MAX_DEG);

            handle.quaternion.copy(drag.rotation)
                .multiply(
                    new THREE.Quaternion()
                        .setFromAxisAngle(
                            new THREE.Vector3(0,0,1),
                            drag.delta*D
                        )
                );

            const q=[...drag.q];

            q[Number(drag.selected)-1]+=drag.delta;

            const pts=points(q);

            ghost.geometry.dispose();

            ghost.geometry=
                new THREE.BufferGeometry()
                    .setFromPoints(pts);

            ghost.visible=true;

            marker.position.copy(pts[4]);
            marker.visible=true;

            status(
                "J"+drag.selected+": "+
                q[Number(drag.selected)-1]
                    .toFixed(2)+" deg"
            );
        }
    });

    // ==================================
    // Execute through existing motion
    // functions. No direct servo bypass.
    // ==================================

    async function execute(state){

        if(sending) return;

        if(Math.abs(state.delta)<0.3) return;

        sending=true;

        const auto=get("ik-auto-pitch");
        const oldAuto=auto.checked;

        const fields=[
            "ik-x","ik-y","ik-z",
            "ik-pitch","ik-elbow"
        ];

        const oldValues=fields.map(id=>get(id).value);

        let accepted=false;

        try{

            if(
                mode!==state.mode ||
                !ready()
            ){
                throw Error("Robot not ready");
            }

            if(mode==="real"){
                await refresh();
            }

            if(
                mode!==state.mode ||
                !ready()
            ){
                throw Error("Robot state changed");
            }

            const q=currentQ();

            if(
                q.some((v,i)=>
                    Math.abs(v-state.q[i])>1
                )
            ){
                throw Error(
                    "Pose changed. Drag again."
                );
            }

            const run=
                mode==="sim" ||
                (
                    state.execute &&
                    realEnable.checked
                );

            if(state.selected==="tcp"){

                // Cartesian control / IK

                const p=fk(q);

                const target={
                    x:p.x,y:p.y,z:p.z
                };

                const a=
                    state.axis.toLowerCase();

                target[a]+=state.delta;

                get("ik-x").value=
                    target.x.toFixed(3);

                get("ik-y").value=
                    target.y.toFixed(3);

                get("ik-z").value=
                    target.z.toFixed(3);

                get("ik-pitch").value=
                    p.pitch.toFixed(3);

                get("ik-elbow").value=
                    q[2]>=0?"up":"down";

                // X/Y retain pitch.
                // Z preserves existing Auto Head.

                auto.checked=
                    a==="z" && oldAuto;

                if(typeof rlAxisIntent!=="undefined"){

                    rlAxisIntent={
                        axis:a,
                        mode,
                        target:{...target}
                    };
                }

                const goal=getIK();

                if(
                    !goal.q ||
                    !goal.q.every(Number.isFinite)
                ){
                    throw Error("Invalid IK solution");
                }

                const predicted=fk(goal.q);

                const error=Math.hypot(
                    predicted.x-target.x,
                    predicted.y-target.y,
                    predicted.z-target.z
                );

                if(error>0.5){
                    throw Error("IK target mismatch");
                }

                if(mode==="real"){
                    planRealIK(goal);
                }

                accepted=true;

                marker.position.set(
                    target.x,target.y,target.z
                );

                marker.visible=true;

                if(!run){

                    status(
                        "IK preview valid. REAL motion disabled."
                    );

                    return;
                }

                if(mode==="real"){

                    await moveIKReal();

                }else{

                    await moveIK();
                }

                if(
                    get("ik-result").classList
                        .contains("error")
                ){
                    throw Error(
                        get("ik-result").textContent
                    );
                }

                status("TCP command finished");

            }else{

                // Direct joint-space control / FK

                const id=Number(state.selected);
                const i=id-1;

                const goal=[...q];

                goal[i]+=state.delta;

                if(mode==="sim"){

                    checkQ(goal);

                    accepted=true;

                    await moveSim([id],goal);

                }else{

                    const s=servo(id);

                    if(
                        !s ||
                        !s.online ||
                        !Number.isInteger(s.zero) ||
                        !Number.isInteger(s.actual)
                    ){
                        throw Error("Servo feedback unavailable");
                    }

                    const target=Math.round(

                        s.zero+

                        (goal[i]-geom.homeAngles[i])*
                        geom.ticksPerDegree[i]/
                        geom.dirs[i]
                    );

                    if(
                        !Number.isInteger(target) ||
                        target<s.min ||
                        target>s.max
                    ){
                        throw Error(
                            "Servo "+id+
                            " exceeds joint limits"
                        );
                    }

                    accepted=true;

                    if(!run){

                        status(
                            "J"+id+" preview: "+
                            target+" ticks. "+
                            "REAL motion disabled."
                        );

                        return;
                    }

                    // Preserve existing MOVE safety
                    // and configured velocity.

                    await moveReal(id,target);
                }

                if(
                    get("fk-result").classList
                        .contains("error")
                ){
                    throw Error(
                        get("fk-result").textContent
                    );
                }

                const actual=currentQ();

                if(!actual){
                    throw Error("Final feedback unavailable");
                }

                status(
                    "J"+id+
                    " target: "+goal[i].toFixed(2)+
                    " deg | Feedback: "+
                    actual[i].toFixed(2)+" deg",

                    Math.abs(actual[i]-goal[i])>2
                );
            }

            if(mode==="real"){
                await refresh();
            }

            ghost.visible=false;
            marker.visible=false;

        }catch(e){

            status(e.message,true);

        }finally{

            auto.checked=oldAuto;

            if(!accepted){

                fields.forEach(
                    (id,i)=>get(id).value=oldValues[i]
                );
            }

            if(typeof rlAxisIntent!=="undefined"){
                rlAxisIntent=null;
            }

            sending=false;

            update();
        }
    }

    gizmo.addEventListener("mouseUp",()=>{

        const state=drag;

        drag=null;

        if(state){
            execute(state);
        }
    });

    renderer.domElement.addEventListener(
        "pointercancel",
        ()=>{

            drag=null;

            if(gizmo.dragging){
                gizmo.pointerUp({button:0});
            }

            orbit.enabled=true;
            realEnable.checked=false;

            status("Drag cancelled");
        }
    );

    // ==================================
    // Camera controls
    // ==================================

    get("rl-view-3d").onclick=
        ()=>view("3d");

    get("rl-view-side").onclick=
        ()=>view("side");

    get("rl-view-top").onclick=
        ()=>view("top");

    get("rl-view-reset").onclick=
        ()=>view("3d");

    new ResizeObserver(resize).observe(stage);

    updateEnvelope();

    selectControl("tcp");

    let lastUpdate=0;

    function frame(time){

        requestAnimationFrame(frame);

        if(!active()){

            realEnable.checked=false;
            gizmo.enabled=false;

            return;
        }

        if(!stage.clientWidth) return;

        if(time-lastUpdate>=100){

            lastUpdate=time;

            try{
                update();
            }catch(e){

                gizmo.enabled=false;
                gizmo.visible=false;

                status(e.message,true);
            }
        }

        orbit.update();

        renderer.render(scene,camera);
    }

    requestAnimationFrame(frame);
}

try{

    main();

}catch(e){

    console.error(e);

    const status=get("rl-workspace-status");

    if(status){
        status.textContent=
            "Workspace error: "+e.message;
    }
}

