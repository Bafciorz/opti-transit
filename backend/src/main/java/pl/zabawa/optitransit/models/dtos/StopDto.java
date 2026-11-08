package pl.zabawa.optitransit.models.dtos;

import lombok.*;

@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@ToString
public class StopDto {
    private String id;
    private String name;
    private String code;
    private String platformCode;
    private Double latitude;
    private Double longitude;
    private String townName;
    private Integer wheelchairBoarding;
}
