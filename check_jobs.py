#!/usr/bin/env python3
"""Quick job status checker — shows all jobs ordered by most recent."""
import asyncio

from src.data.models import Job, AsyncSessionLocal, init_db
from sqlalchemy import select


async def check_jobs() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job).order_by(Job.id.desc()).limit(20)
        )
        jobs = result.scalars().all()

    if not jobs:
        print("No jobs in database yet.")
        return

    print(f"{'#':>4}  {'platform':8}  {'status':12}  {'category':18}  {'price':8}  summary")
    print("-" * 95)
    for j in jobs:
        cat = j.category.value if j.category else "none"
        price = f"${j.client_price:.3f}" if j.client_price is not None else "n/a"
        summary = (j.result_summary or "")[:50]
        print(f"{j.id:>4}  {j.client_platform:8}  {j.status.value:12}  {cat:18}  {price:8}  {summary}")


if __name__ == "__main__":
    asyncio.run(check_jobs())
