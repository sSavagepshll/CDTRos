(() => {
  document.querySelectorAll(".donut-chart").forEach((chart) => {
    const progress = chart.querySelector(".donut-progress");
    const circumference = 2 * Math.PI * 52;
    const value = Math.min(100, Math.max(0, Number(chart.dataset.donutValue) || 0));
    progress.setAttribute("stroke-dasharray", String(circumference));
    progress.setAttribute("stroke-dashoffset", String(circumference * (1 - value / 100)));
  });

  const dataElement = document.querySelector("#performance-data");
  const chart = document.querySelector("#performance-chart");
  if (!dataElement || !chart) return;

  const data = JSON.parse(dataElement.textContent);
  if (!Array.isArray(data) || data.length === 0) return;

  const ns = "http://www.w3.org/2000/svg";
  const width = 900;
  const height = 310;
  const margin = { top: 18, right: 18, bottom: 43, left: 42 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maxValue = Math.max(1, ...data.flatMap((point) => [point.requests, point.items]));
  const axisMax = maxValue <= 4 ? 4 : Math.ceil(maxValue / 4) * 4;

  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");

  const append = (name, attributes, parent = svg) => {
    const element = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    parent.append(element);
    return element;
  };

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = (axisMax / 4) * tick;
    const y = margin.top + plotHeight - (value / axisMax) * plotHeight;
    append("line", {
      x1: margin.left,
      x2: width - margin.right,
      y1: y,
      y2: y,
      class: "chart-gridline",
    });
    const label = append("text", {
      x: margin.left - 12,
      y: y + 4,
      "text-anchor": "end",
      class: "chart-axis-label",
    });
    label.textContent = String(value);
  }

  const pointsFor = (key) =>
    data.map((point, index) => ({
      x: margin.left + (index / Math.max(1, data.length - 1)) * plotWidth,
      y: margin.top + plotHeight - (point[key] / axisMax) * plotHeight,
      value: point[key],
      date: point.date,
      label: point.label,
    }));

  for (const [key, label, className] of [
    ["requests", "Solicitações recebidas", "chart-line-requests"],
    ["items", "Itens cadastrados no sistema de destino", "chart-line-items"],
  ]) {
    const points = pointsFor(key);
    append("polyline", {
      points: points.map((point) => `${point.x},${point.y}`).join(" "),
      class: className,
    });

    for (const point of points) {
      const circle = append("circle", {
        cx: point.x,
        cy: point.y,
        r: 3.2,
        class: className === "chart-line-requests" ? "chart-point-requests" : "chart-point-items",
      });
      const title = append("title", {}, circle);
      title.textContent = `${point.date}: ${point.value} ${label.toLowerCase()}`;
    }
  }

  for (let index = 0; index < data.length; index += 6) {
    const x = margin.left + (index / Math.max(1, data.length - 1)) * plotWidth;
    const label = append("text", {
      x,
      y: height - 13,
      "text-anchor": index === 0 ? "start" : "middle",
      class: "chart-axis-label",
    });
    label.textContent = data[index].label;
  }
  const last = data.length - 1;
  if (last % 6 !== 0) {
    const label = append("text", {
      x: width - margin.right,
      y: height - 13,
      "text-anchor": "end",
      class: "chart-axis-label",
    });
    label.textContent = data[last].label;
  }

  chart.append(svg);
})();
