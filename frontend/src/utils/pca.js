const EPSILON = 1e-9;

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

const norm = (v) => Math.sqrt(dot(v, v));

const normalize = (v) => {
  const length = norm(v) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
};

const multiplyMatrixVector = (matrix, vector) => [
  matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
  matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
  matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
];

const cloneMatrix = (matrix) => matrix.map((row) => [...row]);

const powerIteration = (matrix, iterations = 100) => {
  let vector = normalize([1, 0.6, 0.3]);

  for (let i = 0; i < iterations; i += 1) {
    const multiplied = multiplyMatrixVector(matrix, vector);
    const multipliedNorm = norm(multiplied);

    if (multipliedNorm < EPSILON) {
      break;
    }

    vector = multiplied.map((value) => value / multipliedNorm);
  }

  const eigenvalue = dot(vector, multiplyMatrixVector(matrix, vector));
  return { eigenvalue, eigenvector: vector };
};

const deflateMatrix = (matrix, eigenvalue, eigenvector) => {
  const next = cloneMatrix(matrix);
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < 3; col += 1) {
      next[row][col] -= eigenvalue * eigenvector[row] * eigenvector[col];
    }
  }
  return next;
};

const standardizeRows = (rows) => {
  const means = [0, 0, 0];
  const stds = [0, 0, 0];

  rows.forEach((row) => {
    means[0] += row[0];
    means[1] += row[1];
    means[2] += row[2];
  });

  means[0] /= rows.length;
  means[1] /= rows.length;
  means[2] /= rows.length;

  rows.forEach((row) => {
    stds[0] += (row[0] - means[0]) ** 2;
    stds[1] += (row[1] - means[1]) ** 2;
    stds[2] += (row[2] - means[2]) ** 2;
  });

  stds[0] = Math.sqrt(stds[0] / Math.max(1, rows.length - 1)) || 1;
  stds[1] = Math.sqrt(stds[1] / Math.max(1, rows.length - 1)) || 1;
  stds[2] = Math.sqrt(stds[2] / Math.max(1, rows.length - 1)) || 1;

  const standardized = rows.map((row) => [
    (row[0] - means[0]) / stds[0],
    (row[1] - means[1]) / stds[1],
    (row[2] - means[2]) / stds[2],
  ]);

  return { standardized, means, stds };
};

const covariance3x3 = (rows) => {
  const cov = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  const denominator = Math.max(1, rows.length - 1);

  rows.forEach((row) => {
    for (let i = 0; i < 3; i += 1) {
      for (let j = 0; j < 3; j += 1) {
        cov[i][j] += row[i] * row[j];
      }
    }
  });

  for (let i = 0; i < 3; i += 1) {
    for (let j = 0; j < 3; j += 1) {
      cov[i][j] /= denominator;
    }
  }

  return cov;
};

export const computeRfmPca = (scatterData = []) => {
  if (!Array.isArray(scatterData) || scatterData.length < 2) {
    return {
      points: [],
      explainedVariance: [0, 0, 0],
      components: [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
      ],
    };
  }

  const rows = scatterData.map((point) => [
    Number(point.recency) || 0,
    Number(point.frequency) || 0,
    Number(point.monetary) || 0,
  ]);

  const { standardized } = standardizeRows(rows);
  const covariance = covariance3x3(standardized);

  const first = powerIteration(covariance);
  const second = powerIteration(deflateMatrix(covariance, first.eigenvalue, first.eigenvector));
  const third = powerIteration(
    deflateMatrix(
      deflateMatrix(covariance, first.eigenvalue, first.eigenvector),
      second.eigenvalue,
      second.eigenvector
    )
  );

  const components = [
    normalize(first.eigenvector),
    normalize(second.eigenvector),
    normalize(third.eigenvector),
  ];

  const eigenvalues = [first.eigenvalue, second.eigenvalue, third.eigenvalue].map((value) =>
    Number.isFinite(value) ? Math.max(0, value) : 0
  );
  const varianceTotal = eigenvalues.reduce((sum, value) => sum + value, 0) || 1;
  const explainedVariance = eigenvalues.map((value) => value / varianceTotal);

  const points = standardized.map((row, index) => {
    const pc1 = dot(row, components[0]);
    const pc2 = dot(row, components[1]);
    const pc3 = dot(row, components[2]);
    return {
      ...scatterData[index],
      pc1,
      pc2,
      pc3,
    };
  });

  return { points, explainedVariance, components };
};
