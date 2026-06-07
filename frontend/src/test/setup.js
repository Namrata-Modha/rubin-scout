import '@testing-library/jest-dom';

// jsdom does not implement HTMLCanvasElement.getContext.
// Stub it so makeTex() in SkyMap.jsx doesn't throw.
HTMLCanvasElement.prototype.getContext = () => ({
  createRadialGradient: () => ({
    addColorStop: () => {},
  }),
  fillRect: () => {},
  fillStyle: null,
});
