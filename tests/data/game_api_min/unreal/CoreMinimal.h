// Déclaration MINIMALE d'une partie de l'API C++ d'Unreal Engine 5, écrite par APEX d'après la documentation
// d'Unreal, pour vérifier la SYNTAXE des classes générées (`apex.brain.gamecode`) avec g++ -fsyntax-only : noms,
// signatures, conversions. Ce n'est PAS Unreal : ni l'outil d'en-têtes (UHT) ni le moteur ne sont simulés.
// Fidélités utiles : `Super` désigne la classe parente ; BindAction ne déduit la classe que de l'objet (le
// pointeur de méthode d'une classe parente, `&ACharacter::Jump`, est converti comme dans Unreal).
#pragma once

#include <cstdint>

#define UCLASS(...)
#define UPROPERTY(...)
#define UFUNCTION(...)
#define GENERATED_BODY()
#define TEXT(x) L##x
#define SCENE_QUERY_STAT(Name) FName(L"" #Name)

using int32 = std::int32_t;

struct FName
{
	FName(const wchar_t* Name) {}
};

struct FVector
{
	float X = 0.f, Y = 0.f, Z = 0.f;
	FVector() {}
	FVector(float InX, float InY, float InZ) : X(InX), Y(InY), Z(InZ) {}
	FVector operator+(const FVector& V) const { return *this; }
	FVector operator*(float Scale) const { return *this; }
	FVector GetSafeNormal() const { return *this; }
};

struct FRotator
{
	float Pitch = 0.f, Yaw = 0.f, Roll = 0.f;
	FRotator(float InPitch, float InYaw, float InRoll) : Pitch(InPitch), Yaw(InYaw), Roll(InRoll) {}
};

struct FQuat
{
	static const FQuat Identity;
};

struct FTransform
{
};

struct FColor
{
	static const FColor Red;
};

namespace EAxis
{
	enum Type { None, X, Y, Z };
}

struct FRotationMatrix
{
	explicit FRotationMatrix(const FRotator& Rot) {}
	FVector GetUnitAxis(EAxis::Type Axis) const { return FVector(); }
};

template <typename T>
struct TArray
{
	const T* begin() const { return nullptr; }
	const T* end() const { return nullptr; }
	int32 Num() const { return 0; }
};

template <typename T>
struct TSet
{
	bool Contains(const T& Element) const { return false; }
	void Add(const T& Element) {}
	int32 Num() const { return 0; }
};

class UClass;

template <typename T>
struct TSubclassOf
{
	TSubclassOf(UClass* From) {}
};

struct FMath
{
	static float Abs(float A) { return A; }
	static float Max(float A, float B) { return A; }
	template <typename T>
	static T Clamp(T X, T Min, T Max) { return X; }
	static float FInterpConstantTo(float Current, float Target, float DeltaTime, float InterpSpeed) { return Target; }
};

class UObject
{
public:
	using Super = UObject;
	virtual ~UObject() {}
	template <typename T>
	T* CreateDefaultSubobject(FName SubobjectName) { return nullptr; }
};

class UActorComponent : public UObject
{
};

class USceneComponent : public UActorComponent
{
public:
	void SetupAttachment(USceneComponent* InParent, FName InSocketName = FName(L"")) {}
	void SetRelativeLocation(const FVector& NewLocation) {}
	void SetRelativeRotation(const FRotator& NewRotation) {}
};

namespace ECollisionEnabled
{
	enum Type { NoCollision, QueryOnly, PhysicsOnly, QueryAndPhysics };
}

class UPrimitiveComponent : public USceneComponent
{
public:
	void SetCollisionProfileName(FName InCollisionProfileName) {}
	void SetCollisionEnabled(ECollisionEnabled::Type NewType) {}
};

class UStaticMeshComponent : public UPrimitiveComponent
{
};

class UShapeComponent : public UPrimitiveComponent
{
};

class UBoxComponent : public UShapeComponent
{
public:
	void SetBoxExtent(FVector InBoxExtent, bool bUpdateOverlaps = true) {}
};

class UCapsuleComponent : public UShapeComponent
{
public:
	void InitCapsuleSize(float InRadius, float InHalfHeight) {}
};

class USkeletalMeshComponent : public UPrimitiveComponent
{
};

class UCameraComponent : public USceneComponent
{
};

class USpringArmComponent : public USceneComponent
{
public:
	static const FName SocketName;
	float TargetArmLength = 300.f;
	bool bUsePawnControlRotation = false;
};

class UCharacterMovementComponent : public UActorComponent
{
public:
	bool bOrientRotationToMovement = false;
	float MaxWalkSpeed = 600.f;
	float JumpZVelocity = 420.f;
};

enum EInputEvent { IE_Pressed, IE_Released };

template <typename UserClass>
struct TAxisHandler
{
	using Type = void (UserClass::*)(float);
};

template <typename UserClass>
struct TActionHandler
{
	using Type = void (UserClass::*)();
};

class UInputComponent : public UActorComponent
{
public:
	template <typename UserClass>
	void BindAxis(const FName AxisName, UserClass* Object, typename TAxisHandler<UserClass>::Type Func) {}
	template <typename UserClass>
	void BindAction(const FName ActionName, const EInputEvent KeyEvent, UserClass* Object,
		typename TActionHandler<UserClass>::Type Func) {}
};

namespace EAutoReceiveInput
{
	enum Type { Disabled, Player0 };
}

struct FActorTickFunction
{
	bool bCanEverTick = false;
};

class AActor;

struct FHitResult
{
	FVector ImpactPoint;
	AActor* GetActor() const { return nullptr; }
	bool IsValidBlockingHit() const { return false; }
};

struct FOverlapResult
{
	AActor* GetActor() const { return nullptr; }
};

enum ECollisionChannel { ECC_Visibility, ECC_Camera, ECC_Pawn, ECC_WorldStatic };

struct FCollisionQueryParams
{
	FCollisionQueryParams(FName InTraceTag, bool bInTraceComplex, const AActor* InIgnoreActor) {}
	void AddIgnoredActor(const AActor* InIgnoreActor) {}
};

struct FCollisionShape
{
	static FCollisionShape MakeSphere(float SphereRadius) { return FCollisionShape(); }
};

class UWorld : public UObject
{
public:
	float GetTimeSeconds() const { return 0.f; }
	bool LineTraceSingleByChannel(FHitResult& OutHit, const FVector& Start, const FVector& End,
		ECollisionChannel TraceChannel, const FCollisionQueryParams& Params) const { return false; }
	bool OverlapMultiByChannel(TArray<FOverlapResult>& OutOverlaps, const FVector& Pos, const FQuat& Rot,
		ECollisionChannel TraceChannel, const FCollisionShape& CollisionShape,
		const FCollisionQueryParams& Params) const { return false; }
};

class AController;

class AActor : public UObject
{
public:
	using Super = AActor;
	FActorTickFunction PrimaryActorTick;
	USceneComponent* RootComponent = nullptr;
	virtual void Tick(float DeltaSeconds) {}
	virtual void BeginPlay() {}
	virtual void OnConstruction(const FTransform& Transform) {}
	UWorld* GetWorld() const { return nullptr; }
	AActor* GetOwner() const { return nullptr; }
	AController* GetInstigatorController() const { return nullptr; }
	FVector GetActorForwardVector() const { return FVector(); }
	void AddActorLocalRotation(FRotator DeltaRotation, bool bSweep = false, FHitResult* OutSweepHitResult = nullptr) {}
	void AddActorWorldOffset(FVector DeltaLocation, bool bSweep = false, FHitResult* OutSweepHitResult = nullptr) {}
};

class AController : public AActor
{
public:
	FRotator GetControlRotation() const { return FRotator(0.f, 0.f, 0.f); }
};

class APawn : public AActor
{
public:
	using Super = APawn;
	EAutoReceiveInput::Type AutoPossessPlayer = EAutoReceiveInput::Disabled;
	AController* Controller = nullptr;
	bool bUseControllerRotationYaw = true;
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) {}
	void AddMovementInput(FVector WorldDirection, float ScaleValue = 1.f) {}
	void AddControllerYawInput(float Val) {}
	void AddControllerPitchInput(float Val) {}
};

class ACharacter : public APawn
{
public:
	using Super = ACharacter;
	UCapsuleComponent* GetCapsuleComponent() const { return nullptr; }
	UCharacterMovementComponent* GetCharacterMovement() const { return nullptr; }
	USkeletalMeshComponent* GetMesh() const { return nullptr; }
	virtual void Jump() {}
	virtual void StopJumping() {}
};

class UDamageType : public UObject
{
public:
	static UClass* StaticClass() { return nullptr; }
};

struct UGameplayStatics
{
	static float ApplyPointDamage(AActor* DamagedActor, float BaseDamage, const FVector& HitFromDirection,
		const FHitResult& HitInfo, AController* EventInstigator, AActor* DamageCauser,
		TSubclassOf<UDamageType> DamageTypeClass) { return BaseDamage; }
	static float ApplyDamage(AActor* DamagedActor, float BaseDamage, AController* EventInstigator,
		AActor* DamageCauser, TSubclassOf<UDamageType> DamageTypeClass) { return BaseDamage; }
};

inline void DrawDebugLine(const UWorld* InWorld, const FVector& LineStart, const FVector& LineEnd,
	const FColor& Color, bool bPersistentLines = false, float LifeTime = -1.f) {}
