const request = require("supertest");
const app = require("../server");

test("health", async () => {
  const res = await request(app).get("/health");
  expect(res.body.status).toBe("ok");
});
