from apscheduler.schedulers.background import BackgroundScheduler
from app.services.scraper.vodafone import extract_jobs_with_details_click
from app.services.scraper.save import save_jobs

def scrape_vodafone():
    url = "https://jobs.vodafone.com/careers?domain=vodafone.com&query=Machine+Learning&start=0&location=Egypt&pid=563018686428909&sort_by=solr&filter_include_remote=1"
    jobs = extract_jobs_with_details_click(url)
    save_jobs(jobs, source="vodafone")
    print(f"[Scheduler] Vodafone jobs updated: {len(jobs)} jobs")

def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(scrape_vodafone, "interval", hours=4)
    scheduler.start()
