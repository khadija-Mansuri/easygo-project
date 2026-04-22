from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
import datetime
from django.core.validators import MinValueValidator, MaxValueValidator
import datetime
import random
import string
import uuid
from django.db import IntegrityError
import time



class UserRegistration(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15)
    password = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    # New fields for Aadhar and Profile Photo
    aadhar_number = models.CharField(max_length=12, unique=True, blank=True, null=True,
                                     help_text="12-digit Aadhar card number")
    profile_photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # Track when profile was updated

    def __str__(self):
        return self.email

    def get_profile_photo_url(self):
        """Returns the profile photo URL or default image"""
        if self.profile_photo:
            return self.profile_photo.url
        return '/static/images/default-avatar.jpg'  # Create this default image





class Package(models.Model):
    CATEGORY_CHOICES = [
        ('Adventure', 'Adventure'),
        ('Honeymoon', 'Honeymoon'),
        ('Family', 'Family'),
        ('Spiritual', 'Spiritual'),
        ('SoloTrip', 'SoloTrip'),
    ]

    TRAVEL_MODE_CHOICES = [
        ('bus', 'Bus'),
        ('train', 'Train'),
        ('flight', 'Flight'),
        ('car', 'Car'),
        ('bike', 'Bike'),
    ]

    # Vehicle capacity limits
    VEHICLE_CAPACITY = {
        'car': 7,  # Car max 7 persons
        'bus': 60,  # Bus max 50 persons
        'train': 100,  # Train max 100 persons
        'flight': 200,  # Flight max 200 persons
        'bike': 1,  # Bike max 2 persons
    }

    title = models.CharField(max_length=200)
    source_city = models.CharField(max_length=100, help_text="City where the tour starts", default="Ahmedabad")
    location = models.CharField(max_length=200)
    duration = models.CharField(max_length=100)
    base_price = models.IntegerField(default=5000, help_text="Price per person")
    category = models.CharField(max_length=100, choices=CATEGORY_CHOICES)
    description = models.TextField()
    image = models.ImageField(upload_to='packages/')
    triptime = models.DateTimeField(default=datetime.datetime(2026, 3, 26, tzinfo=timezone.get_current_timezone()))

    # Person limits based on category
    max_persons = models.IntegerField(default=60)
    min_persons = models.IntegerField(default=1)

    # Available seats tracking
    available_seats = models.IntegerField(default=60)

    def __str__(self):
        return self.title


    def is_trip_expired(self):
        """Check if the trip date has passed"""
        if self.triptime and timezone.now() > self.triptime:
            return True
        return False

    def get_trip_status(self):
        """Get trip status with details"""
        if self.is_trip_expired():
            return {
                'status': 'expired',
                'message': 'This trip has already departed',
                'badge_class': 'bg-secondary',
                'icon': 'fa-calendar-times'
            }
        elif (self.triptime - timezone.now()).days <= 7:
            return {
                'status': 'closing_soon',
                'message': f'Closing in {(self.triptime - timezone.now()).days} days',
                'badge_class': 'bg-warning',
                'icon': 'fa-exclamation-triangle'
            }
        else:
            return {
                'status': 'available',
                'message': 'Available for booking',
                'badge_class': 'bg-success',
                'icon': 'fa-check-circle'
            }
    def get_available_travel_modes(self):
        """Get travel modes based on category"""
        if self.category == 'Adventure':
            return ['bus', 'flight']
        elif self.category == 'Spiritual':
            return ['bus', 'train']
        elif self.category == 'Honeymoon':
            return ['flight', 'car']
        elif self.category == 'Family':
            return ['bus', 'train', 'car']
        elif self.category == 'SoloTrip':
            return ['bike']
        return []

    def get_max_persons_for_travel_mode(self, travel_mode):
        """Get maximum persons allowed for specific travel mode"""
        # First check vehicle physical capacity
        vehicle_limit = self.VEHICLE_CAPACITY.get(travel_mode, 60)

        # For Family category with car, limit to 7
        if self.category == 'Family' and travel_mode == 'car':
            return min(7, vehicle_limit, self.available_seats)

        # For Honeymoon with car, limit to 4 (couple + 2 kids)
        elif self.category == 'Honeymoon' and travel_mode == 'car':
            return min(4, vehicle_limit, self.available_seats)

        # For other combinations, use package max or vehicle limit (whichever is smaller)
        return min(self.max_persons, vehicle_limit, self.available_seats)

    def get_price_multiplier(self, persons):
        """Calculate price based on number of persons"""
        if persons <= 1:
            return self.base_price
        elif persons <= 5:
            return self.base_price * persons
        elif persons <= 10:
            # 5% discount for groups of 6-10
            return int(self.base_price * persons * 0.95)
        elif persons <= 20:
            # 10% discount for groups of 11-20
            return int(self.base_price * persons * 0.90)
        else:
            # 15% discount for groups of 21+
            return int(self.base_price * persons * 0.85)

    def check_seat_availability(self, persons, travel_mode=None):
        """Check if requested persons can be accommodated"""
        if travel_mode:
            max_allowed = self.get_max_persons_for_travel_mode(travel_mode)
            if persons > max_allowed:
                return False, f"Maximum {max_allowed} persons allowed for {travel_mode} in {self.category} package"

        if self.available_seats < persons:
            return False, f"Only {self.available_seats} seats available"
        if persons > self.max_persons:
            return False, f"Maximum {self.max_persons} persons allowed for this package"
        if persons < self.min_persons:
            return False, f"Minimum {self.min_persons} person(s) required"
        return True, "Available"


class Booking(models.Model):
    TRAVEL_MODE_CHOICES = [
        ('bus', 'Bus'),
        ('train', 'Train'),
        ('flight', 'Flight'),
        ('car', 'Car'),
        ('bike', 'Bike'),
    ]

    PACKAGE_TYPE_CHOICES = [
        ('standard', 'Standard'),
        ('premium', 'Premium'),
        ('vip', 'VIP'),
    ]

    user = models.ForeignKey(UserRegistration, on_delete=models.CASCADE)
    package = models.ForeignKey(Package, on_delete=models.CASCADE)
    booking_date = models.DateTimeField(auto_now_add=True)
    travel_date = models.CharField(max_length=100)
    persons = models.IntegerField(validators=[MinValueValidator(1)])
    travel_mode = models.CharField(max_length=20, choices=TRAVEL_MODE_CHOICES, default='bus')
    package_type = models.CharField(max_length=20, choices=PACKAGE_TYPE_CHOICES, default='standard')
    total_price = models.IntegerField(default=0)
    message = models.TextField(blank=True, null=True)
    waiting_list_position = models.IntegerField(null=True, blank=True, help_text="Position in waiting list")
    promoted_from_waiting = models.BooleanField(default=False, help_text="Whether booking was promoted from waiting list")
    booking_status = models.CharField(max_length=20, default='confirmed',
                                      choices=[('confirmed', 'Confirmed'),
                                               ('cancelled', 'Cancelled'),
                                               ('pending', 'Pending'),
                                               ('waiting', 'Waiting List')])
    # In models.py inside the Booking class
    payment_screenshot = models.ImageField(upload_to='payment_screenshots/', blank=True, null=True)

    def __str__(self):
        return f"{self.user.name} - {self.package.title} ({self.persons} persons)"

    def get_waiting_list_position(self):
        """Get current position in waiting list"""
        if self.booking_status != 'waiting':
            return None

        # Count all waiting bookings for this package with earlier booking dates
        position = Booking.objects.filter(
            package=self.package,
            booking_status='waiting',
            booking_date__lt=self.booking_date
        ).count() + 1

        return position

    def update_waiting_list_positions(self):
        """Update all waiting list positions for this package"""
        waiting_bookings = Booking.objects.filter(
            package=self.package,
            booking_status='waiting'
        ).order_by('booking_date')

        for idx, booking in enumerate(waiting_bookings, 1):
            booking.waiting_list_position = idx
            booking.message = f"WL{idx}"
            booking.save(update_fields=['waiting_list_position', 'message'])


class PackageDayPlan(models.Model):
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='day_plans')
    day_number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField()
    hotel_name = models.CharField(max_length=200, blank=True)
    breakfast = models.BooleanField(default=True)
    lunch = models.BooleanField(default=True)
    dinner = models.BooleanField(default=True)

    class Meta:
        ordering = ['day_number']

class DayPhoto(models.Model):
    day_plan = models.ForeignKey(PackageDayPlan, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='day_plans/gallery/')
    caption = models.CharField(max_length=100, blank=True)


class Invoice(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='invoice')
    invoice_number = models.CharField(max_length=50, unique=True)
    generated_date = models.DateTimeField(auto_now_add=True)

    # Invoice breakdown
    base_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    package_type_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)
    package_type_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percentage = models.IntegerField(default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=5.0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Payment details
    payment_status = models.CharField(max_length=20, choices=[
        ('paid', 'Paid'),
        ('pending', 'Pending'),
        ('refunded', 'Refunded')
    ], default='pending')
    payment_method = models.CharField(max_length=50, default='Online Payment')

    # Invoice items
    invoice_items = models.JSONField(default=dict)

    # Additional charges if any
    additional_charges = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['invoice_number']),
            models.Index(fields=['booking']),
        ]

    def __str__(self):
        return f"Invoice #{self.invoice_number} - {self.booking.user.name}"

    def generate_invoice_number(self):
        """Generate unique invoice number with retry logic"""
        max_retries = 5
        retry_count = 0

        while retry_count < max_retries:
            # Method 1: Timestamp with milliseconds + random chars
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S%f')[:-3]  # Includes milliseconds
            random_chars = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            invoice_number = f"INV-{timestamp}-{random_chars}"

            # Check if this invoice number already exists
            if not Invoice.objects.filter(invoice_number=invoice_number).exists():
                return invoice_number

            retry_count += 1
            # Add a small delay to ensure different timestamp
            time.sleep(0.01)

        # Method 2: If all retries failed, use UUID (guaranteed unique)
        uuid_part = str(uuid.uuid4()).replace('-', '')[:12].upper()
        return f"INV-{uuid_part}"

    def save(self, *args, **kwargs):
        # Generate invoice number if not set
        if not self.invoice_number:
            self.invoice_number = self.generate_invoice_number()

        # Use try-except to handle any remaining duplicate issues
        try:
            super().save(*args, **kwargs)
        except IntegrityError as e:
            if 'UNIQUE constraint failed' in str(e) and 'invoice_number' in str(e):
                # If duplicate, generate new number using UUID and try again
                uuid_part = str(uuid.uuid4()).replace('-', '')[:12].upper()
                self.invoice_number = f"INV-{uuid_part}"
                super().save(*args, **kwargs)
            else:
                raise


# Add this to your models.py

class PassengerDetail(models.Model):
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other')
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='passengers')
    name = models.CharField(max_length=100)
    age = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(120)])
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    aadhar_number = models.CharField(max_length=12, unique=True, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.booking.id}"

    class Meta:
        ordering = ['id']




class Feedback(models.Model):
    user = models.ForeignKey(UserRegistration, on_delete=models.CASCADE)
    message = models.TextField()
    rating = models.IntegerField(default=5, choices=[(i, i) for i in range(1, 6)])
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False) # Admin can check before showing it live

    def __str__(self):
        return f"{self.user.name} - {self.rating} Stars"




class ContactMessage(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Contact Message'
        verbose_name_plural = 'Contact Messages'

    def __str__(self):
        return f"{self.name} - {self.subject} ({self.created_at.strftime('%Y-%m-%d')})"