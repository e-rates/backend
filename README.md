# E-Rates API

A Django REST Framework application for managing e-rates.

## Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- Git

## Installation & Setup

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/e-rates.git
cd e-rates
```

### 2. Create a virtual environment

**Windows:**
```bash
python -m venv env
env\Scripts\activate
```

**macOS/Linux:**
```bash
python3 -m venv env
source env/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up environment variables

Copy the example environment file and update with your settings:

```bash
# Windows
copy .env.example .env

# macOS/Linux
cp .env.example .env
```

Edit `.env` and update the values as needed.

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Create a superuser (optional)

```bash
python manage.py createsuperuser
```

### 7. Run the development server

```bash
python manage.py runserver
```

The API will be available at `http://127.0.0.1:8000/`

## API Documentation

- Admin Panel: `http://127.0.0.1:8000/admin/`
- API Root: `http://127.0.0.1:8000/api/` (adjust based on your URL configuration)

### Testing with Insomnia

-test the various endpoints using insomnia or postman

## Project Structure

```
e-rates/
├── config/          # Project settings
├── erates/          # Main app
├── env/             # Virtual environment (not in git)
├── db.sqlite3       # Database (not in git)
├── manage.py        # Django management script
└── requirements.txt # Python dependencies
```

## Technologies Used

- Django
- Django REST Framework
- Django REST Framework GIS (if using geographic data)
- SQLite (development) / PostgreSQL (production recommended)

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

