const http = require("http");
const { spawn } = require("child_process");

const PORT = Number(process.env.PORT || 3000);
let botProcess;

const server = http.createServer((req, res) => {
  if (req.url === "/" || req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      status: "ok",
      service: "telegram-instagram-monitor",
      bot: botProcess && !botProcess.killed ? "running" : "stopped"
    }));
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "Not found" }));
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`HTTP health server listening on port ${PORT}`);
});

const pythonCommand = process.platform === "win32" ? "python" : "python3";
botProcess = spawn(pythonCommand, ["bot.py"], {
  cwd: __dirname,
  env: process.env,
  stdio: "inherit"
});

botProcess.on("error", (err) => {
  console.error("Failed to start Python Telegram bot:", err);
  process.exitCode = 1;
});

botProcess.on("exit", (code, signal) => {
  console.log(`Python bot exited. code=${code}, signal=${signal}`);
  server.close(() => process.exit(code ?? 1));
});

function shutdown(signal) {
  console.log(`Received ${signal}; shutting down...`);
  if (botProcess && !botProcess.killed) botProcess.kill("SIGTERM");
  server.close(() => process.exit(0));
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
