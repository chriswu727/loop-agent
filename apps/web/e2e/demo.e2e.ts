import { expect, test } from '@playwright/test';

test('the built-in demo completes its strict contract and replays the Receipt', async ({
  page,
}) => {
  await page.goto('/');

  await page.getByRole('button', { name: /Write a Python script/ }).click();
  await expect(page.getByRole('textbox', { name: 'Publish a task' })).toHaveValue(
    /first 15 Fibonacci numbers/,
  );
  await page.getByText('Advanced controls', { exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Acceptance contract' })).toHaveValue(
    /exactly the first 15 Fibonacci numbers/,
  );

  await page.getByRole('button', { name: 'Run the agent' }).click();
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]+$/);
  await expect(page.getByText('Verified by re-execution')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('Done', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Wrote fib.py; verified it prints the first 15 Fibonacci numbers.').first(),
  ).toBeVisible();

  await page.getByRole('button', { name: /Receipt/ }).click();
  await expect(page.getByText(/Verified by execution/)).toBeVisible();
  await expect(page.getByText(/criterion-001/).first()).toBeVisible();
  await expect(page.getByText(/criterion-002/).first()).toBeVisible();
  await page.getByRole('button', { name: 'Replay checks' }).click();
  await expect(page.getByText('Replay passed')).toBeVisible({ timeout: 30_000 });
});
