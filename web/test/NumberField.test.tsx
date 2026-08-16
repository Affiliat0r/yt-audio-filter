import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import NumberField from "@/components/NumberField";

/** Mirrors real usage: the parent owns the value. */
function Harness({
  initial = 1,
  min = 1,
  max = 99,
  step,
  onChange,
}: {
  initial?: number;
  min?: number;
  max?: number;
  step?: number;
  onChange?: (n: number) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <NumberField
      value={value}
      min={min}
      max={max}
      step={step}
      ariaLabel="loops"
      onChange={(n) => {
        setValue(n);
        onChange?.(n);
      }}
    />
  );
}

const field = () => screen.getByLabelText("loops") as HTMLInputElement;

describe("NumberField", () => {
  it("can be cleared — the reported bug", async () => {
    // "the whole set only appends the numbers. cant delete the 1"
    const user = userEvent.setup();
    render(<Harness initial={1} />);

    await user.clear(field());

    expect(field().value).toBe("");
  });

  it("lets you type a two-digit number over a one-digit one", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness initial={1} onChange={onChange} />);

    await user.clear(field());
    await user.type(field(), "10");

    expect(field().value).toBe("10");
    expect(onChange).toHaveBeenLastCalledWith(10);
  });

  it("does not clamp mid-keystroke", async () => {
    // Typing "25" passes through "2"; a naive min-clamp of 1 is fine here, but
    // typing "10" passes through "1" and then "10" — a per-keystroke clamp to
    // min=10 would have rewritten the first digit.
    const user = userEvent.setup();
    render(<Harness initial={5} min={10} max={99} />);

    await user.clear(field());
    await user.type(field(), "10");

    expect(field().value).toBe("10");
  });

  it("selects on focus so the first keystroke replaces", async () => {
    const user = userEvent.setup();
    render(<Harness initial={7} />);

    await user.click(field());
    await user.keyboard("3");

    expect(field().value).toBe("3");
  });

  it("clamps to max on blur", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness initial={1} max={99} onChange={onChange} />);

    await user.clear(field());
    await user.type(field(), "500");
    await user.tab();

    expect(field().value).toBe("99");
    expect(onChange).toHaveBeenLastCalledWith(99);
  });

  it("clamps to min on blur", async () => {
    const user = userEvent.setup();
    render(<Harness initial={5} min={2} />);

    await user.clear(field());
    await user.type(field(), "0");
    await user.tab();

    expect(field().value).toBe("2");
  });

  it("restores the previous value when left empty", async () => {
    // Blurring an empty box must not produce NaN or 0 downstream.
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness initial={4} onChange={onChange} />);

    await user.clear(field());
    await user.tab();

    expect(field().value).toBe("4");
    expect(onChange).not.toHaveBeenCalledWith(NaN);
  });

  it("accepts decimals when the step allows them", async () => {
    // The ayah gap is 0–5 in halves.
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness initial={0} min={0} max={5} step={0.5} onChange={onChange} />);

    await user.clear(field());
    await user.type(field(), "1.5");
    await user.tab();

    expect(field().value).toBe("1.5");
    expect(onChange).toHaveBeenLastCalledWith(1.5);
  });

  it("adopts an external change while unfocused", async () => {
    const { rerender } = render(
      <NumberField value={3} min={1} max={99} ariaLabel="loops" onChange={() => {}} />
    );
    expect(field().value).toBe("3");

    rerender(
      <NumberField value={8} min={1} max={99} ariaLabel="loops" onChange={() => {}} />
    );

    expect(field().value).toBe("8");
  });
});
