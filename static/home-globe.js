(function () {
    "use strict";

    const canvas = document.getElementById("homeGlobeCanvas");
    const visual = document.getElementById("globeVisual");
    const motionButton = document.getElementById("globeMotionToggle");
    if (!canvas || !visual) return;

    const context = canvas.getContext("2d", { alpha: true });
    if (!context) {
        visual.dataset.state = "error";
        return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const landPolygons = [
        [[-168, 71], [-145, 72], [-126, 62], [-123, 51], [-128, 45], [-118, 31], [-105, 24], [-96, 18], [-85, 21], [-80, 30], [-66, 44], [-54, 51], [-61, 61], [-84, 70], [-111, 73], [-140, 69]],
        [[-82, 13], [-74, 10], [-63, 7], [-52, 4], [-35, -8], [-42, -23], [-53, -35], [-66, -55], [-73, -44], [-78, -20]],
        [[-11, 36], [-7, 44], [4, 50], [16, 56], [29, 59], [39, 54], [31, 46], [18, 41], [7, 37]],
        [[-18, 35], [-4, 37], [13, 33], [29, 31], [42, 13], [50, 4], [41, -18], [30, -34], [17, -35], [5, -27], [-7, -3], [-14, 13]],
        [[28, 68], [54, 72], [81, 73], [111, 70], [143, 62], [168, 57], [158, 48], [139, 43], [128, 35], [121, 24], [105, 12], [92, 21], [78, 9], [68, 22], [54, 26], [45, 39], [34, 46]],
        [[95, 22], [108, 19], [121, 10], [129, 2], [119, -8], [105, -6], [99, 6]],
        [[113, -11], [131, -12], [146, -20], [153, -29], [144, -41], [126, -39], [113, -26]],
        [[-73, 83], [-43, 82], [-18, 70], [-31, 59], [-52, 60], [-64, 70]],
        [[44, -12], [51, -15], [49, -26], [45, -23]],
        [[129, 34], [142, 43], [145, 37], [136, 31]],
    ];
    const routes = [
        [[31.2, 121.5], [37.8, -122.4]],
        [[31.2, 121.5], [51.5, -0.1]],
        [[31.2, 121.5], [1.3, 103.8]],
        [[35.7, 139.7], [-33.9, 151.2]],
        [[22.3, 114.2], [25.2, 55.3]],
    ];

    let width = 0;
    let height = 0;
    let pixelRatio = 1;
    let radius = 0;
    let centerX = 0;
    let centerY = 0;
    let yaw = -1.62;
    let pitch = -0.14;
    let velocityX = 0;
    let velocityY = 0;
    let dragging = false;
    let lastPointerX = 0;
    let lastPointerY = 0;
    let autoplay = !reduceMotion.matches;
    let lastFrame = performance.now();
    let frameId = 0;
    let destroyed = false;

    function toRadians(value) {
        return value * Math.PI / 180;
    }

    function pointInPolygon(lon, lat, polygon) {
        let inside = false;
        for (let current = 0, previous = polygon.length - 1; current < polygon.length; previous = current++) {
            const currentLon = polygon[current][0];
            const currentLat = polygon[current][1];
            const previousLon = polygon[previous][0];
            const previousLat = polygon[previous][1];
            const crosses = (currentLat > lat) !== (previousLat > lat);
            const boundaryLon = (previousLon - currentLon) * (lat - currentLat) / ((previousLat - currentLat) || .00001) + currentLon;
            if (crosses && lon < boundaryLon) inside = !inside;
        }
        return inside;
    }

    function isLand(lon, lat) {
        return landPolygons.some(polygon => pointInPolygon(lon, lat, polygon));
    }

    function unitVector(lat, lon) {
        const latitude = toRadians(lat);
        const longitude = toRadians(lon);
        const latitudeRadius = Math.cos(latitude);
        return {
            x: latitudeRadius * Math.sin(longitude),
            y: -Math.sin(latitude),
            z: latitudeRadius * Math.cos(longitude),
        };
    }

    function deterministicNoise(seed) {
        const value = Math.sin(seed * 12.9898 + 78.233) * 43758.5453;
        return value - Math.floor(value);
    }

    function createLandPoints() {
        const points = [];
        let seed = 1;
        for (let lat = -57; lat <= 79; lat += 2.45) {
            const longitudeStep = 2.35 / Math.max(.42, Math.cos(toRadians(lat)));
            for (let lon = -178; lon <= 178; lon += longitudeStep) {
                seed += 1;
                const jitterLon = (deterministicNoise(seed) - .5) * longitudeStep * .66;
                const jitterLat = (deterministicNoise(seed + 71) - .5) * 1.3;
                const sampleLon = lon + jitterLon;
                const sampleLat = lat + jitterLat;
                if (!isLand(sampleLon, sampleLat)) continue;
                points.push({
                    ...unitVector(sampleLat, sampleLon),
                    size: .58 + deterministicNoise(seed + 131) * .72,
                    tone: deterministicNoise(seed + 251),
                });
            }
        }
        return points;
    }

    const landPoints = createLandPoints();

    function rotate(vector, scale = 1) {
        const cosineYaw = Math.cos(yaw);
        const sineYaw = Math.sin(yaw);
        const cosinePitch = Math.cos(pitch);
        const sinePitch = Math.sin(pitch);
        const xAfterYaw = vector.x * cosineYaw + vector.z * sineYaw;
        const zAfterYaw = -vector.x * sineYaw + vector.z * cosineYaw;
        const yAfterPitch = vector.y * cosinePitch - zAfterYaw * sinePitch;
        const zAfterPitch = vector.y * sinePitch + zAfterYaw * cosinePitch;
        return {
            x: xAfterYaw * scale,
            y: yAfterPitch * scale,
            z: zAfterPitch * scale,
        };
    }

    function project(vector) {
        return {
            x: centerX + vector.x * radius,
            y: centerY + vector.y * radius,
            z: vector.z,
        };
    }

    function resize() {
        const bounds = visual.getBoundingClientRect();
        width = Math.max(240, bounds.width);
        height = Math.max(220, bounds.height - 34);
        pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * pixelRatio);
        canvas.height = Math.round(height * pixelRatio);
        canvas.style.height = `${height}px`;
        context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
        // The reference globe leaves a little more breathing room around its rim.
        radius = Math.min(width, height) * .39;
        centerX = width * .51;
        centerY = height * .49;
        draw(performance.now());
    }

    function drawBackdrop() {
        context.clearRect(0, 0, width, height);

        // Thin orbital rings sit behind the sphere, like the reference treatment.
        [
            { rotation: -.18, flatten: .32, scale: 1.25, color: "rgba(174, 217, 255, .24)" },
            { rotation: .34, flatten: .2, scale: 1.27, color: "rgba(128, 190, 255, .17)" },
            { rotation: -.62, flatten: .15, scale: 1.34, color: "rgba(110, 173, 244, .13)" },
        ].forEach(ring => {
            context.save();
            context.translate(centerX, centerY);
            context.rotate(ring.rotation);
            context.scale(1, ring.flatten);
            context.beginPath();
            context.arc(0, 0, radius * ring.scale, 0, Math.PI * 2);
            context.strokeStyle = ring.color;
            context.lineWidth = 1;
            context.stroke();
            context.restore();
        });

        const halo = context.createRadialGradient(centerX, centerY, radius * .78, centerX, centerY, radius * 1.18);
        halo.addColorStop(0, "rgba(58, 138, 240, 0)");
        halo.addColorStop(.72, "rgba(58, 138, 240, .07)");
        halo.addColorStop(1, "rgba(58, 138, 240, 0)");
        context.fillStyle = halo;
        context.beginPath();
        context.arc(centerX, centerY, radius * 1.18, 0, Math.PI * 2);
        context.fill();

        context.save();
        context.translate(centerX, centerY + radius * 1.08);
        context.scale(1, .16);
        const groundGlow = context.createRadialGradient(0, 0, 0, 0, 0, radius * 1.08);
        groundGlow.addColorStop(0, "rgba(149, 210, 255, .2)");
        groundGlow.addColorStop(.34, "rgba(80, 157, 230, .1)");
        groundGlow.addColorStop(1, "rgba(33, 102, 190, 0)");
        context.fillStyle = groundGlow;
        context.beginPath();
        context.arc(0, 0, radius * 1.08, 0, Math.PI * 2);
        context.fill();
        context.restore();

        const ocean = context.createRadialGradient(
            centerX - radius * .28,
            centerY - radius * .34,
            radius * .08,
            centerX,
            centerY,
            radius
        );
        ocean.addColorStop(0, "#183653");
        ocean.addColorStop(.48, "#102945");
        ocean.addColorStop(.82, "#091a30");
        ocean.addColorStop(1, "#040d1a");
        context.fillStyle = ocean;
        context.beginPath();
        context.arc(centerX, centerY, radius, 0, Math.PI * 2);
        context.fill();
        context.save();
        context.strokeStyle = "rgba(203, 231, 255, .8)";
        context.shadowColor = "rgba(104, 179, 255, .6)";
        context.shadowBlur = 18;
        context.lineWidth = 1.15;
        context.stroke();
        context.restore();
    }

    function drawVisibleLine(points, strokeStyle, lineWidth) {
        context.beginPath();
        let drawing = false;
        points.forEach(point => {
            const transformed = rotate(point);
            if (transformed.z <= .015) {
                drawing = false;
                return;
            }
            const screen = project(transformed);
            if (!drawing) {
                context.moveTo(screen.x, screen.y);
                drawing = true;
            } else {
                context.lineTo(screen.x, screen.y);
            }
        });
        context.strokeStyle = strokeStyle;
        context.lineWidth = lineWidth;
        context.stroke();
    }

    function drawGrid() {
        for (let lat = -60; lat <= 60; lat += 30) {
            const line = [];
            for (let lon = -180; lon <= 180; lon += 3) line.push(unitVector(lat, lon));
            drawVisibleLine(line, "rgba(116, 182, 240, .16)", .7);
        }
        for (let lon = -150; lon <= 180; lon += 30) {
            const line = [];
            for (let lat = -88; lat <= 88; lat += 3) line.push(unitVector(lat, lon));
            drawVisibleLine(line, "rgba(116, 182, 240, .14)", .7);
        }
    }

    function drawLand() {
        const visible = [];
        landPoints.forEach(point => {
            const transformed = rotate(point);
            if (transformed.z > -.01) visible.push({ point, transformed });
        });
        visible.sort((left, right) => left.transformed.z - right.transformed.z);
        visible.forEach(({ point, transformed }) => {
            const screen = project(transformed);
            const depth = Math.max(0, transformed.z);
            const alpha = Math.min(1, .34 + depth * .72);
            const size = point.size * (.72 + depth * .62);
            context.fillStyle = point.tone > .62
                ? `rgba(226, 244, 255, ${alpha})`
                : `rgba(125, 194, 255, ${Math.min(1, alpha * 1.02)})`;
            context.beginPath();
            context.arc(screen.x, screen.y, size, 0, Math.PI * 2);
            context.fill();
        });
    }

    function sphericalInterpolation(start, end, amount) {
        const dot = Math.max(-1, Math.min(1, start.x * end.x + start.y * end.y + start.z * end.z));
        const omega = Math.acos(dot);
        if (omega < .0001) return start;
        const sineOmega = Math.sin(omega);
        const startWeight = Math.sin((1 - amount) * omega) / sineOmega;
        const endWeight = Math.sin(amount * omega) / sineOmega;
        return {
            x: start.x * startWeight + end.x * endWeight,
            y: start.y * startWeight + end.y * endWeight,
            z: start.z * startWeight + end.z * endWeight,
        };
    }

    function drawRoutes(time) {
        context.save();
        context.setLineDash([3, 6]);
        context.lineDashOffset = autoplay ? -(time * .014) % 9 : 0;
        routes.forEach((route, index) => {
            const start = unitVector(route[0][0], route[0][1]);
            const end = unitVector(route[1][0], route[1][1]);
            const line = [];
            for (let step = 0; step <= 42; step += 1) {
                const amount = step / 42;
                const point = sphericalInterpolation(start, end, amount);
                const altitude = 1 + Math.sin(Math.PI * amount) * (.07 + index * .009);
                line.push({ x: point.x * altitude, y: point.y * altitude, z: point.z * altitude });
            }
            drawVisibleLine(line, "rgba(117, 195, 255, .56)", .85);
        });
        context.restore();
    }

    function drawLight() {
        const highlight = context.createRadialGradient(
            centerX - radius * .4,
            centerY - radius * .45,
            0,
            centerX - radius * .28,
            centerY - radius * .28,
            radius * .92
        );
        highlight.addColorStop(0, "rgba(214, 239, 255, .16)");
        highlight.addColorStop(.36, "rgba(146, 206, 255, .055)");
        highlight.addColorStop(1, "rgba(4, 10, 18, 0)");
        context.fillStyle = highlight;
        context.beginPath();
        context.arc(centerX, centerY, radius - 1, 0, Math.PI * 2);
        context.fill();
    }

    function drawReflection() {
        const reflectionY = centerY + radius * 1.03;
        const reflection = context.createRadialGradient(centerX, reflectionY, 0, centerX, reflectionY, radius * .82);
        reflection.addColorStop(0, "rgba(185, 224, 255, .13)");
        reflection.addColorStop(.42, "rgba(87, 160, 227, .055)");
        reflection.addColorStop(1, "rgba(20, 72, 139, 0)");
        context.save();
        context.translate(centerX, reflectionY);
        context.scale(1, .11);
        context.fillStyle = reflection;
        context.beginPath();
        context.arc(0, 0, radius * .82, 0, Math.PI * 2);
        context.fill();
        context.restore();
    }

    function draw(time) {
        drawBackdrop();
        drawGrid();
        drawRoutes(time);
        drawLand();
        drawLight();
        drawReflection();
    }

    function animate(time) {
        if (destroyed) return;
        const delta = Math.min(40, time - lastFrame);
        lastFrame = time;
        if (!dragging) {
            if (autoplay) yaw += delta * .000085;
            yaw += velocityX;
            pitch = Math.max(-.72, Math.min(.72, pitch + velocityY));
            velocityX *= .93;
            velocityY *= .9;
        }
        draw(time);
        frameId = requestAnimationFrame(animate);
    }

    function updateMotionButton() {
        if (!motionButton) return;
        motionButton.textContent = autoplay ? "暂停自转" : "开启自转";
        motionButton.setAttribute("aria-pressed", String(!autoplay));
    }

    function toggleAutoplay() {
        autoplay = !autoplay;
        updateMotionButton();
    }

    canvas.tabIndex = 0;
    canvas.addEventListener("pointerdown", event => {
        dragging = true;
        lastPointerX = event.clientX;
        lastPointerY = event.clientY;
        velocityX = 0;
        velocityY = 0;
        canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", event => {
        if (!dragging) return;
        const deltaX = event.clientX - lastPointerX;
        const deltaY = event.clientY - lastPointerY;
        yaw += deltaX * .0065;
        pitch = Math.max(-.72, Math.min(.72, pitch + deltaY * .0052));
        velocityX = deltaX * .00055;
        velocityY = deltaY * .00038;
        lastPointerX = event.clientX;
        lastPointerY = event.clientY;
    });
    canvas.addEventListener("pointerup", event => {
        dragging = false;
        if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointercancel", () => {
        dragging = false;
    });
    canvas.addEventListener("keydown", event => {
        const step = .09;
        if (event.key === "ArrowLeft") yaw -= step;
        else if (event.key === "ArrowRight") yaw += step;
        else if (event.key === "ArrowUp") pitch = Math.max(-.72, pitch - step);
        else if (event.key === "ArrowDown") pitch = Math.min(.72, pitch + step);
        else if (event.key === " " || event.key === "Enter") toggleAutoplay();
        else return;
        event.preventDefault();
    });
    motionButton?.addEventListener("click", toggleAutoplay);

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(visual);
    reduceMotion.addEventListener("change", event => {
        autoplay = !event.matches;
        updateMotionButton();
    });
    window.addEventListener("pagehide", () => {
        destroyed = true;
        cancelAnimationFrame(frameId);
        resizeObserver.disconnect();
    }, { once: true });

    updateMotionButton();
    resize();
    visual.dataset.state = "ready";
    frameId = requestAnimationFrame(animate);
}());
