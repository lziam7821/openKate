import { describe, expect, it } from "vitest";

import { parseCommaList } from "./foundation-utils";

describe("parseCommaList", () => {
  it("normalizes comma separated project references", () => {
    expect(parseCommaList("shop.test, payments.test, ")).toEqual(["shop.test", "payments.test"]);
  });
});
