#!/usr/bin/env node

const tasks = [
  { id: "T-1", title: "Ship docs", status: "open" },
  { id: "T-2", title: "Repair worker", status: "closed" },
  { id: "T-3", title: "Add tests", status: "open" },
];

const args = process.argv.slice(2);
if (args[0] !== "list") {
  console.error("usage: task list");
  process.exitCode = 2;
} else {
  for (const task of tasks) console.log(`${task.id}\t${task.status}\t${task.title}`);
}
