#!/usr/bin/env python3
import asyncio
import sys
sys.path.insert(0, 'src')

from data.models import Job, JobStatus, async_session, init_db
from sqlalchemy import select


async def check_jobs():
    await init_db()
    async with async_session() as session:
        # Get pending/active jobs
        result = await session.execute(
            select(Job).where(
                Job.status.in_([
                    JobStatus.PENDING,
                    JobStatus.CLASSIFIED,
                    JobStatus.QUOTED,
                    JobStatus.ACCEPTED,
                    JobStatus.MATCHING,
                    JobStatus.IN_PROGRESS
                ])
            ).order_by(Job.created_at.desc())
        )
        jobs = result.scalars().all()
        
        if not jobs:
            print("No pending jobs found.")
            return
        
        print(f"Found {len(jobs)} pending/active jobs:\n")
        for job in jobs:
            print(f"ID: {job.id}")
            print(f"Status: {job.status}")
            print(f"Description: {job.description[:100]}...")
            print(f"Category: {job.task_category}")
            print(f"Created: {job.created_at}")
            print("-" * 60)


if __name__ == "__main__":
    asyncio.run(check_jobs())
