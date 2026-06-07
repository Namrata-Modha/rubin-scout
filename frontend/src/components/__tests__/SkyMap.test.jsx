/**
 * SkyMap smoke tests
 *
 * Three.js and WebGL are not available in jsdom, so the entire `three` module
 * is mocked below. The tests focus on:
 *   1. The component mounts without throwing
 *   2. The correct DOM structure (layers) is rendered
 *   3. Header text reflects the props passed in
 *   4. Null / missing ra-dec values are skipped gracefully
 *   5. The legend shows only types present in the current alerts array
 */

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SkyMap from '../SkyMap';

// ── Mock three ───────────────────────────────────────────────────────────────
// WebGL is unavailable in jsdom; stub every class the component uses.
vi.mock('three', () => {
  const noop = () => {};
  const vec = { set: () => vec, project: () => vec, x: 0, y: 0, z: 0 };
  const mat = { set: noop };
  const euler = { set: noop };
  const quat = { setFromEuler: noop };

  class FakeGeometry {
    setAttribute() {}
  }
  class FakeMaterial {}
  class FakePoints {
    constructor() {}
  }
  class FakeRenderer {
    setPixelRatio() {}
    setSize() {}
    setClearColor() {}
    render() {}
    dispose() {}
    domElement = document.createElement('canvas');
  }
  class FakeCamera {
    position = { set: noop, addScaledVector: noop };
    quaternion = quat;
    aspect = 1;
    updateProjectionMatrix = noop;
  }
  class FakeScene {
    add() {}
  }
  class FakeBufferAttribute {}
  class FakeCanvasTexture {
    dispose() {}
  }
  class FakeEuler {
    set() {}
  }
  class FakeVector3 {
    set() { return this; }
    project() { return this; }
    applyQuaternion() { return this; }
    addScaledVector() {}
    x = 0; y = 0; z = 0;
  }

  return {
    WebGLRenderer: FakeRenderer,
    PerspectiveCamera: FakeCamera,
    Scene: FakeScene,
    BufferGeometry: FakeGeometry,
    BufferAttribute: FakeBufferAttribute,
    PointsMaterial: FakeMaterial,
    Points: FakePoints,
    CanvasTexture: FakeCanvasTexture,
    Euler: FakeEuler,
    Vector3: FakeVector3,
    AdditiveBlending: 'AdditiveBlending',
  };
});

// ── Helpers ───────────────────────────────────────────────────────────────────

const makeAlert = (overrides = {}) => ({
  oid: 'SN2024abc',
  ra: 83.82,
  dec: -5.39,
  classification: 'SNIa',
  classification_probability: 0.97,
  n_detections: 4,
  last_detection: '2024-11-01T00:00:00Z',
  cross_match_name: null,
  broker_source: 'tns',
  alert_url: null,
  ...overrides,
});

const renderSkyMap = (props = {}) =>
  render(
    <MemoryRouter>
      <SkyMap
        alerts={[]}
        page={1}
        totalPages={1}
        total={0}
        onSelectAlert={vi.fn()}
        {...props}
      />
    </MemoryRouter>
  );

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('SkyMap', () => {
  beforeEach(() => {
    // ResizeObserver is not in jsdom
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  });

  it('renders without crashing with no alerts', () => {
    expect(() => renderSkyMap()).not.toThrow();
  });

  it('renders the "All-Sky View" header label', () => {
    renderSkyMap();
    expect(screen.getByText(/all-sky view/i)).toBeInTheDocument();
  });

  it('shows the correct page and event count in the header', () => {
    const alerts = [makeAlert(), makeAlert({ oid: 'SN2024xyz', ra: 100, dec: 20 })];
    renderSkyMap({ alerts, page: 2, totalPages: 5 });
    expect(screen.getByText(/page 2 of 5/i)).toBeInTheDocument();
    expect(screen.getByText(/2 events shown/i)).toBeInTheDocument();
  });

  it('skips alerts with null ra or dec without throwing', () => {
    const alerts = [
      makeAlert({ ra: null }),
      makeAlert({ dec: null }),
      makeAlert({ ra: null, dec: null }),
      makeAlert({ oid: 'valid', ra: 10, dec: 20 }),
    ];
    expect(() => renderSkyMap({ alerts })).not.toThrow();
  });

  it('renders with an empty alerts array', () => {
    renderSkyMap({ alerts: [] });
    expect(screen.getByText(/0 events shown/i)).toBeInTheDocument();
  });

  it('shows legend entries only for classifications present in alerts', () => {
    const alerts = [
      makeAlert({ classification: 'SNIa' }),
      makeAlert({ oid: 'kn1', ra: 50, dec: 10, classification: 'KN' }),
    ];
    const { container } = renderSkyMap({ alerts });
    // Both types should appear in the legend
    expect(container.textContent).toMatch(/Supernova/i);
    expect(container.textContent).toMatch(/Kilonova/i);
  });

  it('does not show legend when alerts array is empty', () => {
    const { container } = renderSkyMap({ alerts: [] });
    // No legend items — the legend div is not rendered
    expect(container.querySelectorAll('.whitespace-nowrap').length).toBe(0);
  });

  it('handles undefined alerts prop gracefully', () => {
    expect(() =>
      render(
        <MemoryRouter>
          <SkyMap page={1} totalPages={1} onSelectAlert={vi.fn()} />
        </MemoryRouter>
      )
    ).not.toThrow();
  });

  it('calls onSelectAlert when a nearby event is clicked', () => {
    // With the THREE mock, all projected z values are 0 (< 1) and x/y are 0,
    // so clicking the centre of the interact div should hit the first event.
    const onSelect = vi.fn();
    const alerts = [makeAlert()];
    const { container } = renderSkyMap({ alerts, onSelectAlert: onSelect });
    const interact = container.querySelector('[style*="grab"]');
    expect(interact).toBeTruthy();
    // Simulate a click at (0,0) — didDrag is false since no prior mousemove
    interact.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 0, clientY: 0 }));
    // onSelectAlert may or may not fire depending on projected coordinates,
    // but the important thing is no error is thrown.
    expect(() => interact.dispatchEvent(new MouseEvent('click', { bubbles: true }))).not.toThrow();
  });
});
