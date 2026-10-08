const express = require("express");
const { exec } = require("child_process");

const app = express();

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

app.get("/ping", (req, res) => {
  const host = req.query.host;
  exec(`ping -c 1 ${host}`, (err, stdout) => {
    if (err) {
      return res.status(500).send("ping failed");
    }
    res.send(stdout);
  });
});

module.exports = app;
