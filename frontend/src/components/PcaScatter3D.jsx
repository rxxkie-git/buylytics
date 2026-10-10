import { alpha, Box, Chip, Stack, Typography, useTheme } from "@mui/material";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatCurrency } from "../utils/formatters";

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const rotatePoint = (point, yaw, pitch) => {
  const cosY = Math.cos(yaw);
  const sinY = Math.sin(yaw);
  const cosP = Math.cos(pitch);
  const sinP = Math.sin(pitch);

  const x1 = point.x * cosY - point.z * sinY;
  const z1 = point.x * sinY + point.z * cosY;

  const y2 = point.y * cosP - z1 * sinP;
  const z2 = point.y * sinP + z1 * cosP;

  return { x: x1, y: y2, z: z2 };
};

const projectPoint = (point, width, height) => {
  const distance = 4;
  const perspective = 1 / (distance - point.z);
  const scale = Math.min(width, height) * 0.46;

  return {
    x: width / 2 + point.x * scale * perspective,
    y: height / 2 - point.y * scale * perspective,
    perspective,
  };
};

const drawAxis = (ctx, from, to, label, color) => {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(to.x, to.y);
  ctx.stroke();

  ctx.fillStyle = color;
  ctx.font = "12px 'Segoe UI', sans-serif";
  ctx.fillText(label, to.x + 6, to.y - 6);
};

const drawBackdrop = (ctx, width, height, theme) => {
  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, alpha(theme.palette.background.paper, theme.palette.mode === "dark" ? 0.94 : 0.97));
  gradient.addColorStop(0.55, alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.17 : 0.09));
  gradient.addColorStop(1, alpha(theme.palette.secondary.main, theme.palette.mode === "dark" ? 0.17 : 0.07));

  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  const glow = ctx.createRadialGradient(width * 0.2, height * 0.15, 40, width * 0.55, height * 0.55, width * 0.8);
  glow.addColorStop(0, alpha(theme.palette.info.main, 0.22));
  glow.addColorStop(1, "transparent");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = alpha(theme.palette.divider, theme.palette.mode === "dark" ? 0.22 : 0.4);
  ctx.lineWidth = 0.7;
  ctx.setLineDash([4, 7]);
  for (let index = 1; index <= 6; index += 1) {
    const y = (height / 7) * index;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  ctx.setLineDash([]);
};

const defaultAxisMeta = [
  {
    axisLabel: "PC1: Overall Customer Value",
  },
  {
    axisLabel: "PC2: Engagement Pattern",
  },
  {
    axisLabel: "PC3: Special-Case Variation",
  },
];

const getAxisMeta = (axisMeta, variance) =>
  [0, 1, 2].map((index) => {
    const fallback = defaultAxisMeta[index];
    const axis = Array.isArray(axisMeta) ? axisMeta[index] : null;
    const baseLabel =
      axis?.axisLabel ||
      axis?.shortLabel ||
      `${fallback.axisLabel} (${Math.round((variance[index] || 0) * 100)}%)`;
    return baseLabel;
  });

const PcaScatter3D = ({
  points,
  variance = [0, 0, 0],
  emphasis = "balanced",
  highlightedSegment = "all",
  axisMeta = [],
}) => {
  const theme = useTheme();
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const projectedRef = useRef([]);
  const dragRef = useRef({ active: false, x: 0, y: 0 });
  const velocityRef = useRef({ yaw: 0, pitch: 0 });
  const [size, setSize] = useState({ width: 640, height: 460 });
  const [rotation, setRotation] = useState({ yaw: -0.8, pitch: 0.35 });
  const [targetRotation, setTargetRotation] = useState({ yaw: -0.8, pitch: 0.35 });
  const [isDragging, setIsDragging] = useState(false);
  const [hovered, setHovered] = useState(null);
  const axisLabels = useMemo(() => getAxisMeta(axisMeta, variance), [axisMeta, variance]);

  const preparedPoints = useMemo(() => {
    if (!Array.isArray(points) || points.length === 0) {
      return [];
    }

    const maxAbs = Math.max(
      1,
      ...points.map((point) => Math.max(Math.abs(point.pc1), Math.abs(point.pc2), Math.abs(point.pc3)))
    );

    return points.map((point) => ({
      ...point,
      x: point.pc1 / maxAbs,
      y: point.pc2 / maxAbs,
      z: point.pc3 / maxAbs,
      active:
        highlightedSegment === "all" ||
        String(point.segment || "").toLowerCase() === String(highlightedSegment).toLowerCase(),
    }));
  }, [points, highlightedSegment]);

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      setSize({
        width: Math.max(280, Math.floor(entry.contentRect.width)),
        height: Math.max(420, Math.floor(entry.contentRect.height)),
      });
    });

    resizeObserver.observe(containerRef.current);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    let frameId = null;

    const animateToTarget = () => {
      let shouldContinue = false;
      setRotation((current) => {
        const nextYaw = current.yaw + (targetRotation.yaw - current.yaw) * 0.24;
        const nextPitch = current.pitch + (targetRotation.pitch - current.pitch) * 0.24;
        const settled =
          Math.abs(nextYaw - targetRotation.yaw) < 0.0009 &&
          Math.abs(nextPitch - targetRotation.pitch) < 0.0009;

        if (settled) {
          return current;
        }

        shouldContinue = true;
        return { yaw: nextYaw, pitch: nextPitch };
      });

      if (shouldContinue) {
        frameId = requestAnimationFrame(animateToTarget);
      }
    };

    frameId = requestAnimationFrame(animateToTarget);
    return () => {
      if (frameId) {
        cancelAnimationFrame(frameId);
      }
    };
  }, [targetRotation]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    const pixelRatio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(size.width * pixelRatio);
    canvas.height = Math.floor(size.height * pixelRatio);
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }

    ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    ctx.clearRect(0, 0, size.width, size.height);
    drawBackdrop(ctx, size.width, size.height, theme);

    const originRotated = rotatePoint({ x: 0, y: 0, z: 0 }, rotation.yaw, rotation.pitch);
    const xAxisRotated = rotatePoint({ x: 1.7, y: 0, z: 0 }, rotation.yaw, rotation.pitch);
    const yAxisRotated = rotatePoint({ x: 0, y: 1.7, z: 0 }, rotation.yaw, rotation.pitch);
    const zAxisRotated = rotatePoint({ x: 0, y: 0, z: 1.7 }, rotation.yaw, rotation.pitch);

    const axisFrom = projectPoint(originRotated, size.width, size.height);
    drawAxis(
      ctx,
      axisFrom,
      projectPoint(xAxisRotated, size.width, size.height),
      axisLabels[0],
      theme.palette.info.main
    );
    drawAxis(
      ctx,
      axisFrom,
      projectPoint(yAxisRotated, size.width, size.height),
      axisLabels[1],
      theme.palette.success.main
    );
    drawAxis(
      ctx,
      axisFrom,
      projectPoint(zAxisRotated, size.width, size.height),
      axisLabels[2],
      theme.palette.warning.main
    );

    const transformed = preparedPoints
      .map((point) => {
        const rotated = rotatePoint(point, rotation.yaw, rotation.pitch);
        const projected = projectPoint(rotated, size.width, size.height);

        const baseSize = emphasis === "monetary" ? Math.min(14, 4 + (Number(point.monetary) || 0) / 280) : 6;

        return {
          ...point,
          depth: rotated.z,
          sx: projected.x,
          sy: projected.y,
          size: baseSize * projected.perspective,
        };
      })
      .sort((a, b) => a.depth - b.depth);

    projectedRef.current = transformed;

    transformed.forEach((point) => {
      const color = point.active ? point.fill || theme.palette.primary.main : alpha(theme.palette.text.disabled, 0.45);
      const depthAlpha = clamp((point.depth + 1.7) / 3.4, 0.24, 1);
      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.globalAlpha = point.active ? 0.3 + depthAlpha * 0.6 : 0.12;
      ctx.shadowColor = point.active ? alpha(color, 0.55) : "transparent";
      ctx.shadowBlur = point.active ? 16 * depthAlpha : 0;
      ctx.arc(point.sx, point.sy, Math.max(1.2, point.size), 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = alpha("#FFFFFF", point.active ? 0.22 : 0.08);
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.globalAlpha = 1;
    });
  }, [preparedPoints, rotation, size, theme, axisLabels, emphasis]);

  const mapEventToCanvas = useCallback((event) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
  }, []);

  const updateHover = useCallback((x, y) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      setHovered(null);
      return;
    }

    let nearest = null;
    let nearestDistance = 18;

    projectedRef.current.forEach((point) => {
      const distance = Math.hypot(point.sx - x, point.sy - y);
      if (distance < nearestDistance) {
        nearest = point;
        nearestDistance = distance;
      }
    });

    setHovered(
      nearest
        ? {
            x: nearest.sx,
            y: nearest.sy,
            customerId: nearest.customerId,
            segment: nearest.segment,
            cluster: nearest.cluster,
            monetary: nearest.monetary,
            recency: nearest.recency,
            frequency: nearest.frequency,
          }
        : null
    );
  }, []);

  const handlePointerDown = useCallback(
    (event) => {
      const { x, y } = mapEventToCanvas(event);
      dragRef.current = { active: true, x, y };
      velocityRef.current = { yaw: 0, pitch: 0 };
      setIsDragging(true);
      setHovered(null);
      event.currentTarget.setPointerCapture?.(event.pointerId);
    },
    [mapEventToCanvas]
  );

  const handlePointerMove = useCallback(
    (event) => {
      const { x, y } = mapEventToCanvas(event);

      if (dragRef.current.active) {
        const dx = x - dragRef.current.x;
        const dy = y - dragRef.current.y;

        velocityRef.current = {
          yaw: dx * 0.0016,
          pitch: dy * 0.0014,
        };

        setTargetRotation((current) => ({
          yaw: current.yaw + dx * 0.012,
          pitch: clamp(current.pitch + dy * 0.011, -1.2, 1.2),
        }));

        dragRef.current = { active: true, x, y };
        return;
      }

      updateHover(x, y);
    },
    [mapEventToCanvas, updateHover]
  );

  const releaseDrag = useCallback(() => {
    if (dragRef.current.active) {
      setTargetRotation((current) => ({
        yaw: current.yaw + velocityRef.current.yaw * 9,
        pitch: clamp(current.pitch + velocityRef.current.pitch * 9, -1.2, 1.2),
      }));
    }

    dragRef.current = { active: false, x: 0, y: 0 };
    velocityRef.current = { yaw: 0, pitch: 0 };
    setIsDragging(false);
  }, []);

  const handlePointerUp = useCallback(
    (event) => {
      releaseDrag();
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    },
    [releaseDrag]
  );

  const handlePointerLeave = useCallback(() => {
    releaseDrag();
    setHovered(null);
  }, [releaseDrag]);

  return (
    <Box ref={containerRef} sx={{ position: "relative", width: "100%", height: "100%" }}>
      <canvas
        ref={canvasRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerLeave}
        style={{
          width: "100%",
          height: "100%",
          borderRadius: 18,
          border: `1px solid ${alpha(theme.palette.divider, theme.palette.mode === "dark" ? 0.7 : 0.9)}`,
          cursor: isDragging ? "grabbing" : "grab",
          touchAction: "none",
        }}
      />

      <Stack direction="row" spacing={1} sx={{ position: "absolute", top: 10, left: 10 }}>
        <Chip
          size="small"
          variant="outlined"
          label="Drag to rotate"
          sx={{
            bgcolor: alpha(theme.palette.background.default, theme.palette.mode === "dark" ? 0.26 : 0.42),
          }}
        />
        <Chip
          size="small"
          variant="outlined"
          label="Hover for details"
          sx={{
            bgcolor: alpha(theme.palette.background.default, theme.palette.mode === "dark" ? 0.26 : 0.42),
          }}
        />
      </Stack>

      {hovered ? (
        <Box
          sx={{
            position: "absolute",
            left: Math.max(10, Math.min(size.width - 210, hovered.x + 14)),
            top: Math.max(10, Math.min(size.height - 110, hovered.y + 14)),
            p: 1,
            borderRadius: 2,
            width: 200,
            bgcolor: alpha(theme.palette.background.paper, theme.palette.mode === "dark" ? 0.96 : 0.98),
            border: `1px solid ${theme.palette.divider}`,
            boxShadow: 6,
          }}
        >
          <Typography variant="caption" color="text.secondary">
            Customer {hovered.customerId || "-"}
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 700 }}>
            {hovered.segment || "Unknown segment"}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.4 }}>
            Cluster: {hovered.cluster}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            R: {hovered.recency} | F: {hovered.frequency} | M: {formatCurrency(hovered.monetary || 0)}
          </Typography>
        </Box>
      ) : null}
    </Box>
  );
};

export default PcaScatter3D;
